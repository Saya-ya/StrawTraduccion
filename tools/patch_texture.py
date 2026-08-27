#!/usr/bin/env python3
"""Parcheador de texturas para Strawberry Panic!

Dado un manifiesto, lee archivos originales de Data.bin, los descomprime,
reemplaza las pictures indicadas con las codificadas desde PNG editado,
recomprime y devuelve los nuevos streams listos para inyectar en la FAT.

Piezas:
  decompress (LZ77)  -> tools/lz77.py
  parse TIM2         -> dev/tim2_parser.py
  encode PNG->TIM2   -> dev/tim2_encode.py
  recomprimir (LZ77) -> tools/lz77.py

Uso:
    python3 dev/patch_texture.py --manifest texturas/manifest.json \
                                 --data originales/Data.bin \
                                 --dry-run

Sin --dry-run, escribe los streams en --out-dir (uno por file_id).
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEV = Path(__file__).resolve().parent
for p in (TOOLS, DEV):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from datafat import read_entries          # type: ignore
from lz77 import compress, decompress     # type: ignore
from tim2_parser import find_tim2_files, Tim2Picture, Tim2ParseError
from tim2_encode import encode_picture, read_png_rgba, validate_round_trip


def _lz77_stream_size(stream: bytes, offset: int = 0) -> int:
    if stream[offset:offset + 4] != b"LZ77" or offset + 12 > len(stream):
        raise ValueError(f"No hay LZ77 valido en 0x{offset:X}")
    comp_size = int.from_bytes(stream[offset + 8:offset + 12], "little")
    size = 12 + comp_size
    if comp_size <= 0 or offset + size > len(stream):
        raise ValueError(f"LZ77 truncado en 0x{offset:X}")
    return size


# ---------------------------------------------------------------------------
# Procesar un archivo
# ---------------------------------------------------------------------------

def _get_file_blob(data_bin: Path, file_id: int, rows: list | None = None) -> Tuple[bytes, bool, dict]:
    """Lee un archivo por ID desde Data.bin. Devuelve (blob_descomprimido, was_lz77, fat_row)."""
    if rows is None:
        rows = read_entries(str(data_bin))
    row = next((r for r in rows if r["id"] == file_id and r["is_file"]), None)
    if row is None:
        raise ValueError(f"ID {file_id} no encontrado en {data_bin}")

    with open(data_bin, "rb") as f:
        f.seek(row["off"])
        raw = f.read(row["size"])

    if raw[:4] == b"LZ77":
        blob = decompress(raw, strict=False)
        return blob, True, row
    return raw, False, row


def _locate_picture(blob: bytes, tim2_index: int, picture_index: int) -> Tim2Picture:
    """Encuentra una picture especifica en un blob descomprimido."""
    tm2s = find_tim2_files(blob)
    if tim2_index < 0 or tim2_index >= len(tm2s):
        raise ValueError(
            f"TIM2 {tim2_index} fuera de rango: el archivo tiene {len(tm2s)} TIM2"
        )
    tm2 = tm2s[tim2_index]
    if picture_index < 0 or picture_index >= len(tm2.pictures):
        raise ValueError(
            f"Picture {picture_index} fuera de rango: TIM2 {tim2_index} tiene "
            f"{len(tm2.pictures)} pictures"
        )
    return tm2.pictures[picture_index]


def _apply_one_patch(
    blob: bytearray,
    pic: Tim2Picture,
    png_path: Path,
    mode: str,
) -> dict:
    """Codifica el PNG y hace splice del nuevo image_data en el blob."""
    width, height, rgba = read_png_rgba(png_path)

    if width != pic.width or height != pic.height:
        raise ValueError(
            f"Dimensiones del PNG ({width}x{height}) no coinciden con la textura "
            f"({pic.width}x{pic.height})"
        )

    image_data, clut_data = encode_picture(pic, rgba, mode=mode)

    if len(image_data) != pic.image_size:
        raise ValueError(
            f"image_data encoded ({len(image_data)}) no coincide con original "
            f"({pic.image_size}) en modo preserve_palette"
        )

    rt = validate_round_trip(pic, rgba, image_data)
    if not rt["identical"] and rt["pct_diff"] > 1.0:
        print(
            f"  ⚠ Round-trip: {rt['pct_diff']}% pixeles distintos "
            f"(max diff por canal={rt['max_per_channel_diff']}) — "
            f"posible edicion fuera de paleta",
            file=sys.stderr,
        )

    image_offset = pic.picture_offset + pic.header_size
    blob[image_offset : image_offset + len(image_data)] = image_data

    if clut_data is not None:
        clut_offset = image_offset + pic.image_size
        blob[clut_offset : clut_offset + len(clut_data)] = clut_data

    return rt


def process_file(
    data_bin: Path,
    file_id: int,
    patches: List[dict],
    rows: list | None = None,
) -> Tuple[int, bytes]:
    """Procesa un file_id: descomprime, parchea, recomprime y devuelve nuevo stream.

    Args:
        data_bin: Ruta a Data.bin (original).
        file_id: ID del archivo con texturas a parchear.
        patches: Lista de dicts con {tim2_index, picture_index, png, mode}.

    Returns:
        (file_id, compressed_stream) con el nuevo stream listo para _inject_one.
    """
    nested_patches = [p for p in patches if p.get("lz77_offset") is not None]
    direct_patches = [p for p in patches if p.get("lz77_offset") is None]

    if nested_patches:
        if direct_patches:
            raise ValueError("No mezcles parches directos y lz77_offset en el mismo file_id")

        if rows is None:
            rows = read_entries(str(data_bin))
        row = next((r for r in rows if r["id"] == file_id and r["is_file"]), None)
        if row is None:
            raise ValueError(f"ID {file_id} no encontrado en {data_bin}")

        with open(data_bin, "rb") as f:
            f.seek(row["off"])
            raw = bytearray(f.read(row["size"]))

        by_offset: Dict[int, List[dict]] = {}
        for patch in nested_patches:
            by_offset.setdefault(int(patch["lz77_offset"]), []).append(patch)

        for nested_off, group in by_offset.items():
            old_size = _lz77_stream_size(raw, nested_off)
            blob = decompress(bytes(raw[nested_off:nested_off + old_size]), strict=False)
            mutable = bytearray(blob)

            for patch in group:
                tim2_idx = patch.get("tim2_index", 0)
                pic_idx = patch.get("picture_index", 0)
                png_path = Path(patch["png"])
                mode = patch.get("mode", "preserve_palette")
                if not png_path.exists():
                    raise FileNotFoundError(f"PNG no encontrado: {png_path}")
                pic = _locate_picture(bytes(mutable), tim2_idx, pic_idx)
                _apply_one_patch(mutable, pic, png_path, mode)

            new_stream = compress(bytes(mutable))
            redec = decompress(new_stream)
            if redec != bytes(mutable):
                raise RuntimeError(f"Round-trip LZ77 anidado fallo en 0x{nested_off:X}")
            if len(new_stream) > old_size:
                raise ValueError(
                    f"LZ77 anidado 0x{nested_off:X} no cabe ({len(new_stream):,} > {old_size:,})"
                )
            raw[nested_off:nested_off + len(new_stream)] = new_stream
            raw[nested_off + len(new_stream):nested_off + old_size] = b"\x00" * (old_size - len(new_stream))

        return file_id, bytes(raw)

    blob, was_lz77, _ = _get_file_blob(data_bin, file_id, rows)
    mutable = bytearray(blob)

    for patch in patches:
        tim2_idx = patch.get("tim2_index", 0)
        pic_idx = patch.get("picture_index", 0)
        png_path = Path(patch["png"])
        mode = patch.get("mode", "preserve_palette")

        if not png_path.exists():
            raise FileNotFoundError(f"PNG no encontrado: {png_path}")

        pic = _locate_picture(bytes(mutable), tim2_idx, pic_idx)
        rt = _apply_one_patch(mutable, pic, png_path, mode)

        if rt["identical"]:
            print(f"  ✓ ID {file_id} T{tim2_idx} P{pic_idx}: round-trip identico")
        else:
            print(
                f"  ○ ID {file_id} T{tim2_idx} P{pic_idx}: {rt['pct_diff']}% diffs "
                f"(max={rt['max_per_channel_diff']})"
            )

    new_blob = bytes(mutable)
    if was_lz77:
        compressed = compress(new_blob)
        redec = decompress(compressed)
        if len(redec) != len(new_blob) or redec != new_blob:
            diffs = sum(a != b for a, b in zip(new_blob, redec))
            raise RuntimeError(
                f"Recompresion round-trip fallo: {diffs} diffs en {len(new_blob)} bytes"
            )
        return file_id, compressed

    return file_id, new_blob


# ---------------------------------------------------------------------------
# Procesar manifiesto completo
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: Path) -> List[dict]:
    """Carga y valida el manifiesto de parches."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifiesto no encontrado: {manifest_path}")

    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("El manifiesto debe ser una lista JSON")

    for i, e in enumerate(entries):
        for field in ("file_id", "png"):
            if field not in e:
                raise ValueError(f"Entrada {i} del manifiesto no tiene '{field}'")
        e.setdefault("tim2_index", 0)
        e.setdefault("picture_index", 0)
        e.setdefault("mode", "preserve_palette")
        if "lz77_offset" in e and e["lz77_offset"] is not None:
            e["lz77_offset"] = int(e["lz77_offset"])

    return entries


def process_manifest(
    data_bin: Path,
    manifest_path: Path,
    verbose: bool = True,
) -> list[dict]:
    """Procesa todo el manifiesto y devuelve lista de dicts con stats.

    Cada dict: {file_id, stream, patches: [{tim2_index, picture_index, png, roundtrip, ...}]}
    """
    entries = load_manifest(manifest_path)

    by_file: Dict[int, List[dict]] = {}
    for e in entries:
        by_file.setdefault(e["file_id"], []).append(e)

    rows = read_entries(str(data_bin))
    results: list[dict] = []
    for file_id, patches in by_file.items():
        if verbose:
            print(f"\nProcesando ID {file_id} ({len(patches)} parches)...")
        try:
            fid, stream = process_file(data_bin, file_id, patches, rows=rows)
            results.append({
                "file_id": fid,
                "stream": stream,
                "patches": patches,
            })
            if verbose:
                print(f"  → Stream: {len(stream):,} bytes")
        except Exception as exc:
            if verbose:
                print(f"  ✗ Error: {exc}", file=sys.stderr)
            results.append({
                "file_id": file_id,
                "stream": None,
                "patches": patches,
                "error": str(exc),
            })

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parcheador de texturas: decompress -> splice -> compress",
    )
    ap.add_argument("--manifest", required=True, help="Ruta a manifest.json")
    ap.add_argument("--data", default=str(ROOT / "originales" / "Data.bin"),
                    help="Ruta a Data.bin original")
    ap.add_argument("--out-dir", default=str(ROOT / "dev" / "output" / "patched"),
                    help="Directorio donde escribir los streams patcheados")
    ap.add_argument("--dry-run", action="store_true",
                    help="Solo validar sin escribir archivos")
    args = ap.parse_args(argv)

    manifest_path = Path(args.manifest)
    data_bin = Path(args.data)

    if not data_bin.exists():
        print(f"No existe: {data_bin}", file=sys.stderr)
        return 1

    results = process_manifest(data_bin, manifest_path)

    if results and not args.dry_run:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r["stream"] is not None:
                out_path = out_dir / f"ID_{r['file_id']:05d}.lz77"
                out_path.write_bytes(r["stream"])
                print(f"Escrito: {out_path} ({len(r['stream']):,} bytes)")
            else:
                print(f"  ✗ Saltado ID {r['file_id']}: {r.get('error', 'desconocido')}")

    ok = sum(1 for r in results if r["stream"] is not None)
    print(f"\nProcesados: {ok}/{len(results)} archivos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

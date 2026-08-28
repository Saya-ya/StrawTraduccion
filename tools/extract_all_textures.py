#!/usr/bin/env python3
"""Extrae TODAS las texturas TIM2 de originales/Data.bin a PNG + metadatos.

Pensado para inventariar el juego y localizar texto de menú / UI. Escribe:

    <out>/png/ID_<id>_T<tim2>_P<pic>_<w>x<h>.png
    <out>/textures.csv
    <out>/textures.json

Cada registro incluye métricas útiles para filtrar (dimensiones, formato,
colores únicos, ratio de alpha, aspect ratio, score_ui, score_font).

Uso:
    python3 dev/extract_all_textures.py --out dev/output/all_textures
    python3 dev/extract_all_textures.py --out dev/output/all_textures --max-dim 512
    python3 dev/extract_all_textures.py --out dev/output/all_textures --ids 9000-9029,8070
    python3 dev/extract_all_textures.py --out dev/output/all_textures --no-png   # solo metadatos
"""

from __future__ import annotations

from pathlib import Path
import argparse
import csv
import hashlib
import json
import sys
import time
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEV = Path(__file__).resolve().parent
for p in (TOOLS, DEV):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from datafat import read_entries  # type: ignore
from lz77 import decompress  # type: ignore
from tim2_parser import find_tim2_files, Tim2Picture
from tim2_png import picture_to_rgba, composite_rgba_over_background, write_png_rgba

DEFAULT_DATA_BIN = ROOT / "originales" / "Data.bin"
DEFAULT_OUT = ROOT / "work_texturas" / "output" / "all_textures"


def unique_index_count(pic: Tim2Picture) -> int | None:
    if pic.image_type == 5:
        return len(set(pic.image_data[: pic.width * pic.height]))
    if pic.image_type in (3, 4):
        vals = set()
        for b in pic.image_data:
            vals.add(b & 0x0F)
            vals.add((b >> 4) & 0x0F)
        return len(vals)
    return None


def metrics_from_rgba(rgba: bytes) -> tuple[float, float]:
    """Devuelve (alpha_ratio, nonzero_ratio) usando slicing en C (rápido).

    Para fuentes/UI blancas con alpha, el canal de color suele ser 255 en todos
    los pixeles (incluso los transparentes), así que la métrica útil es el alpha.
    Reportamos nonzero_ratio = alpha_ratio para mantener el esquema de columnas.
    """
    n = len(rgba) // 4
    if n == 0:
        return 0.0, 0.0
    alpha = rgba[3::4]              # bytes con todos los canales alpha
    alpha_nz = n - alpha.count(0)  # count() es a nivel C
    ratio = alpha_nz / n
    return ratio, ratio


def rgba_content_hash(width: int, height: int, rgba: bytes) -> str:
    h = hashlib.sha256()
    h.update(f"{width}x{height}".encode("ascii"))
    h.update(b"\0")
    h.update(rgba)
    return h.hexdigest()


def score_ui(pic: Tim2Picture, unique: int | None, alpha_ratio: float, aspect: float) -> tuple[int, list[str]]:
    """Heurística para 'texto de menú / UI / strip de texto'."""
    score = 0
    reasons: list[str] = []
    w, h = pic.width, pic.height

    if pic.image_type in (3, 4, 5):
        score += 6; reasons.append("indexada")
    # Strips de texto: mucho más anchos que altos, y no muy altos.
    if aspect >= 3.0 and h <= 64:
        score += 40; reasons.append("strip_texto")
    elif aspect >= 2.0 and h <= 96:
        score += 25; reasons.append("strip_ancho")
    elif aspect >= 1.5 and h <= 128:
        score += 10; reasons.append("horizontal")
    # Altura típica de líneas de texto.
    if h in (16, 24, 32, 40, 48, 56, 64):
        score += 12; reasons.append(f"altura_linea={h}")
    # Transparencia: los textos/UI suelen tener bastante alpha 0.
    if 0.02 <= alpha_ratio <= 0.75:
        score += 12; reasons.append(f"alpha_util={alpha_ratio:.2f}")
    elif alpha_ratio < 0.005:
        score -= 8; reasons.append("opaca")
    # Pocos colores => probable texto/UI plana.
    if unique is not None:
        if unique <= 4:
            score += 14; reasons.append(f"muy_pocos_colores={unique}")
        elif unique <= 16:
            score += 10; reasons.append(f"pocos_colores={unique}")
        elif unique <= 48:
            score += 3; reasons.append(f"colores={unique}")
        else:
            score -= 6; reasons.append(f"muchos_colores={unique}")
    # Penaliza sprites/CG grandes casi de pantalla completa.
    if w >= 512 and h >= 384:
        score -= 25; reasons.append("pantalla_completa")
    return score, reasons


def score_font(pic: Tim2Picture, unique: int | None) -> int:
    score = 0
    w, h = pic.width, pic.height
    if pic.image_type in (3, 4, 5):
        score += 10
    if w == h and w in (128, 256, 512):
        score += 30
    if w % 16 == 0 and h % 16 == 0:
        score += 8
    if unique is not None and unique <= 20:
        score += 10
    return score


def parse_ids_spec(spec: str) -> set[int]:
    ids: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return ids


def iter_nested_lz77(raw: bytes):
    """Yield valid LZ77 streams embedded inside a raw container.

    Some menu containers are not LZ77 themselves, but contain a small table followed
    by several LZ77 streams. The direct extractor used to miss those textures.
    """
    pos = 0
    while True:
        off = raw.find(b"LZ77", pos)
        if off < 0:
            return
        pos = off + 4
        if off + 12 > len(raw):
            continue
        comp_size = int.from_bytes(raw[off + 8:off + 12], "little")
        stream_size = 12 + comp_size
        if comp_size <= 0 or off + stream_size > len(raw):
            continue
        try:
            blob = decompress(raw[off:off + stream_size], strict=True)
        except Exception:
            continue
        yield off, stream_size, blob


def extract_all(
    data_path: Path,
    out_dir: Path,
    *,
    write_png: bool = True,
    max_dim: int | None = None,
    only_ids: set[int] | None = None,
    background: tuple[int, int, int] | None = None,
    progress_every: int = 100,
    progress_callback=None,
) -> list[dict]:
    rows = [r for r in read_entries(data_path) if r["is_file"]]
    if only_ids is not None:
        rows = [r for r in rows if r["id"] in only_ids]

    png_dir = out_dir / "png"
    if write_png:
        png_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    t0 = time.time()
    processed = 0
    if progress_callback:
        progress_callback(processed, len(rows), len(records), 0.0)
    with data_path.open("rb") as f:
        for r in rows:
            f.seek(r["off"])
            raw = f.read(r["size"])
            candidates = []
            if raw[:4] == b"LZ77":
                try:
                    blob = decompress(raw, strict=False)
                    candidates.append((blob, True, None, None))
                except Exception:
                    candidates = []
            else:
                candidates.append((raw, False, None, None))
                for nested_off, nested_size, blob in iter_nested_lz77(raw):
                    candidates.append((blob, True, nested_off, nested_size))

            for blob, compressed, nested_off, nested_size in candidates:
                tm2s = find_tim2_files(blob)
                for ti, tm2 in enumerate(tm2s):
                    for pic in tm2.pictures:
                        unique = unique_index_count(pic)
                        aspect = pic.width / pic.height if pic.height else 0.0
                        rgba = None
                        alpha_ratio = nonzero_ratio = None
                        png_rel = None
                        content_hash = None
                        too_big = max_dim is not None and (pic.width > max_dim or pic.height > max_dim)
                        try:
                            rgba = picture_to_rgba(pic, force_opaque=False)
                            content_hash = rgba_content_hash(pic.width, pic.height, rgba)
                            alpha_ratio, nonzero_ratio = metrics_from_rgba(rgba)
                        except Exception:
                            rgba = None
                        su, ui_reasons = score_ui(
                            pic, unique,
                            alpha_ratio if alpha_ratio is not None else 0.0,
                            aspect,
                        )
                        sf = score_font(pic, unique)

                        if write_png and rgba is not None and not too_big:
                            nested_part = f"_L{nested_off:06X}" if nested_off is not None else ""
                            name = f"ID_{r['id']:05d}{nested_part}_T{ti:03d}_P{pic.index:02d}_{pic.width}x{pic.height}.png"
                            out_rgba = composite_rgba_over_background(rgba, background) if background else rgba
                            write_png_rgba(png_dir / name, pic.width, pic.height, out_rgba)
                            png_rel = f"png/{name}"

                        records.append({
                            "id": r["id"],
                            "fat_row": r["row"],
                            "file_offset": r["off"],
                            "raw_size": len(raw),
                            "compressed": compressed,
                            "nested_lz77_offset": nested_off,
                            "nested_lz77_size": nested_size,
                            "tim2_index": ti,
                            "tim2_offset": tm2.offset,
                            "picture_index": pic.index,
                            "width": pic.width,
                            "height": pic.height,
                            "aspect": round(aspect, 3),
                            "image_type": pic.image_type,
                            "image_type_name": pic.image_type_name,
                            "image_size": pic.image_size,
                            "clut_size": pic.clut_size,
                            "clut_colors": pic.clut_color_count,
                            "unique_colors": unique,
                            "alpha_ratio": None if alpha_ratio is None else round(alpha_ratio, 4),
                            "nonzero_ratio": None if nonzero_ratio is None else round(nonzero_ratio, 4),
                            "score_ui": su,
                            "score_font": sf,
                            "ui_reasons": ",".join(ui_reasons),
                            "too_big": too_big,
                            "png": png_rel,
                            "content_hash": content_hash,
                        })
            processed += 1
            if progress_callback:
                progress_callback(processed, len(rows), len(records), time.time() - t0)
            if progress_every and processed % progress_every == 0:
                print(f"  ... {processed}/{len(rows)} archivos, {len(records)} texturas, {time.time()-t0:.1f}s")

    records.sort(key=lambda r: (-r["score_ui"], r["id"], r["tim2_index"], r["picture_index"]))
    return records


def write_outputs(out_dir: Path, records: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "textures.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    if records:
        fields = list(records[0].keys())
        with (out_dir / "textures.csv").open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            wr.writerows(records)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extrae todas las texturas TIM2 de Data.bin a PNG + metadatos")
    ap.add_argument("--data-bin", default=str(DEFAULT_DATA_BIN))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-png", action="store_true", help="Solo genera CSV/JSON (rápido)")
    ap.add_argument("--max-dim", type=int, default=None, help="No exporta PNG de texturas con lado mayor a este valor (igual las cataloga)")
    ap.add_argument("--ids", type=str, default=None, help="Limita a IDs: '9000-9029,8070,15000-15110'")
    ap.add_argument("--bg", "--background", dest="background", default=None, help="Compone alpha sobre fondo RRGGBB en los PNG (por defecto conserva alpha)")
    ap.add_argument("--catalog", action="store_true", help="Genera también index.html al terminar")
    args = ap.parse_args(argv)

    data_path = Path(args.data_bin)
    if not data_path.exists():
        print(f"Error: no existe {data_path}", file=sys.stderr)
        return 1

    only_ids = parse_ids_spec(args.ids) if args.ids else None
    background = None
    if args.background:
        v = args.background.strip().lstrip("#")
        background = (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))

    out_dir = Path(args.out)
    print(f"Extrayendo texturas de {data_path} -> {out_dir}")
    records = extract_all(
        data_path, out_dir,
        write_png=not args.no_png,
        max_dim=args.max_dim,
        only_ids=only_ids,
        background=background,
    )
    write_outputs(out_dir, records)
    n_png = sum(1 for r in records if r["png"])
    print(f"Texturas catalogadas: {len(records)} (PNG escritos: {n_png})")
    print(f"Metadatos: {out_dir/'textures.csv'} , {out_dir/'textures.json'}")

    if args.catalog:
        try:
            from texture_catalog import build_catalog
            html = build_catalog(out_dir)
            print(f"Catálogo: {html}")
        except Exception as e:
            print(f"No se pudo generar catálogo automáticamente: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

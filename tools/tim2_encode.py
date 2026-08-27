"""Codificador PNG -> TIM2 para Strawberry Panic!

Inverso de tim2_png.py. Soporta dos modos:
- preserve_palette: conserva la CLUT original, mapea cada pixel PNG al indice
  de paleta mas cercano. Ideal para "repintar texto" con los mismos colores.
- rebuild_palette: cuantiza PNG a N colores y reconstruye CLUT. Permite colores
  nuevos (mantiene mismo numero de colores que el original). NO IMPLEMENTADO.

Uso:
    python3 dev/tim2_encode.py <png> <data_bin> <file_id> [tim2_index [pic_index]]

Requiere: tim2_parser, tim2_png (en dev/).
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import struct
import sys
import zlib
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEV = Path(__file__).resolve().parent
for p in (TOOLS, DEV):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tim2_parser import (
    Tim2Picture,
    Tim2File,
    find_tim2_files,
    parse_tim2,
)
from tim2_png import (
    decode_clut_rgba32,
    picture_to_rgba,
    write_png_rgba,
)

RGBA = Tuple[int, int, int, int]

def read_png_rgba(filepath: str | Path) -> Tuple[int, int, bytes]:
    data = Path(filepath).read_bytes()

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("No es un PNG valido (magic incorrecto)")

    pos = 8
    width = height = 0
    color_type = 2
    idat_parts: List[bytes] = []

    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("PNG truncado")
        chunk_len = struct.unpack_from(">I", data, pos)[0]
        chunk_type = data[pos + 4 : pos + 8]
        pos += 8

        if chunk_type == b"IHDR":
            if chunk_len < 13:
                raise ValueError("IHDR truncado")
            width = struct.unpack_from(">I", data, pos)[0]
            height = struct.unpack_from(">I", data, pos + 4)[0]
            color_type = data[pos + 9]

        elif chunk_type == b"IDAT":
            idat_parts.append(data[pos : pos + chunk_len])

        elif chunk_type == b"IEND":
            break

        pos += chunk_len + 4

    if width <= 0 or height <= 0:
        raise ValueError("Falta IHDR o dims invalidas")

    raw = zlib.decompress(b"".join(idat_parts))
    src_channels = 4 if color_type == 6 else 3
    row_stride = width * src_channels + 1
    if len(raw) < height * row_stride:
        raise ValueError("Datos descomprimidos truncados")

    rgba = bytearray(width * height * 4)
    out = 0

    for y in range(height):
        base = y * row_stride
        filt = raw[base]
        row = raw[base + 1 : base + row_stride]

        if filt == 0:  # None
            target = row
        elif filt == 1:  # Sub
            target = _unfilter_sub(row, width, src_channels)
        elif filt == 2:  # Up
            target = _unfilter_up(row, rgba, out, width, y, src_channels)
        elif filt == 3:  # Average
            target = _unfilter_avg(row, rgba, out, width, y, src_channels)
        elif filt == 4:  # Paeth
            target = _unfilter_paeth(row, rgba, out, width, y, src_channels)
        else:
            raise ValueError(f"Filtro PNG no soportado: {filt}")

        if color_type == 6:
            rgba[out : out + width * 4] = target
        else:
            for x in range(width):
                s = x * 3
                d = out + x * 4
                rgba[d] = target[s]
                rgba[d + 1] = target[s + 1]
                rgba[d + 2] = target[s + 2]
                rgba[d + 3] = 255
        out += width * 4

    return width, height, bytes(rgba)


def _unfilter_sub(row: bytes, width: int, channels: int = 4) -> bytes:
    out = bytearray(len(row))
    for x in range(width):
        s = x * channels
        for c in range(channels):
            left = out[s + c - channels] if x > 0 else 0
            out[s + c] = (row[s + c] + left) & 0xFF
    return bytes(out)


def _unfilter_up(row: bytes, prev: bytearray, out: int, width: int, y: int, channels: int = 4) -> bytes:
    result = bytearray(len(row))
    for x in range(width):
        s = x * channels
        dst = out + x * 4
        for c in range(channels):
            up = prev[dst + c - width * 4] if y > 0 else 0
            result[s + c] = (row[s + c] + up) & 0xFF
    return bytes(result)


def _unfilter_avg(row: bytes, prev: bytearray, out: int, width: int, y: int, channels: int = 4) -> bytes:
    result = bytearray(len(row))
    for x in range(width):
        s = x * channels
        dst = out + x * 4
        for c in range(channels):
            left = result[s + c - channels] if x > 0 else 0
            up = prev[dst + c - width * 4] if y > 0 else 0
            avg = (left + up) // 2
            result[s + c] = (row[s + c] + avg) & 0xFF
    return bytes(result)


def _unfilter_paeth(row: bytes, prev: bytearray, out: int, width: int, y: int, channels: int = 4) -> bytes:
    result = bytearray(len(row))
    for x in range(width):
        s = x * channels
        dst = out + x * 4
        for c in range(channels):
            left = result[s + c - channels] if x > 0 else 0
            up = prev[dst + c - width * 4] if y > 0 else 0
            up_left = prev[dst + c - width * 4 - 4] if (x > 0 and y > 0) else 0
            p = left + up - up_left
            pa = abs(p - left)
            pb = abs(p - up)
            pc = abs(p - up_left)
            pr = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
            result[s + c] = (row[s + c] + pr) & 0xFF
    return bytes(result)

def _color_distance(c1: RGBA, c2: RGBA) -> int:
    dr = c1[0] - c2[0]
    dg = c1[1] - c2[1]
    db = c1[2] - c2[2]
    da = c1[3] - c2[3]
    return dr * dr + dg * dg + db * db + da * da * 4


def _closest_index(rgba_color: RGBA, palette: List[RGBA]) -> int:
    best = 0
    best_d = 1 << 30
    for i, pc in enumerate(palette):
        d = _color_distance(rgba_color, pc)
        if d < best_d:
            best_d = d
            best = i
            if d == 0:
                break
    return best

def _encode_indexed8_preserve(pic: Tim2Picture, rgba: bytes) -> bytes:
    palette = decode_clut_rgba32(pic, unswizzle=True, force_opaque=False)
    n = pic.width * pic.height
    out = bytearray(n)
    for i in range(n):
        c: RGBA = (rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3])
        idx = _closest_index(c, palette)
        out[i] = idx & 0xFF
    return bytes(out)


def _encode_indexed4_preserve(pic: Tim2Picture, rgba: bytes) -> bytes:
    palette = decode_clut_rgba32(pic, unswizzle=False, force_opaque=False)
    max_c = min(pic.clut_color_count or 16, 16)
    palette = palette[:max_c]
    w, h = pic.width, pic.height
    out = bytearray()
    for i in range(0, w * h, 2):
        c1: RGBA = (rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3])
        low = _closest_index(c1, palette) & 0x0F
        if i + 1 < w * h:
            j = i + 1
            c2: RGBA = (rgba[j * 4], rgba[j * 4 + 1], rgba[j * 4 + 2], rgba[j * 4 + 3])
            high = _closest_index(c2, palette) & 0x0F
        else:
            high = 0
        out.append(low | (high << 4))
    return bytes(out)

def encode_picture(
    pic: Tim2Picture,
    rgba: bytes,
    mode: str = "preserve_palette",
) -> Tuple[bytes, Optional[bytes]]:
    expected = pic.width * pic.height * 4
    if len(rgba) != expected:
        raise ValueError(
            f"RGBA no coincide: {len(rgba)} bytes para {pic.width}x{pic.height} "
            f"(esperados {expected})"
        )

    if mode == "preserve_palette":
        if pic.image_type == 5:
            return _encode_indexed8_preserve(pic, rgba), None
        if pic.image_type in (3, 4):
            return _encode_indexed4_preserve(pic, rgba), None
        raise NotImplementedError(
            f"image_type={pic.image_type} no soportado en preserve_palette"
        )
    if mode == "rebuild_palette":
        raise NotImplementedError("rebuild_palette no implementado")

    raise ValueError(f"Modo desconocido: {mode}")

def _clone_pic(pic: Tim2Picture, image_data: bytes, clut_data: bytes | None = None) -> Tim2Picture:
    return Tim2Picture(
        index=pic.index,
        tim2_offset=pic.tim2_offset,
        picture_offset=pic.picture_offset,
        total_size=pic.header_size + len(image_data) + (len(clut_data) if clut_data else pic.clut_size),
        clut_size=len(clut_data) if clut_data else pic.clut_size,
        image_size=len(image_data),
        header_size=pic.header_size,
        clut_color_count=pic.clut_color_count,
        picture_format=pic.picture_format,
        mipmap_count=pic.mipmap_count,
        clut_type=pic.clut_type,
        image_type=pic.image_type,
        width=pic.width,
        height=pic.height,
        gs_tex0=pic.gs_tex0,
        gs_tex1=pic.gs_tex1,
        gs_regs=pic.gs_regs,
        extra_header=pic.extra_header,
        image_data=image_data,
        clut_data=clut_data if clut_data is not None else pic.clut_data,
    )


def validate_round_trip(pic: Tim2Picture, rgba: bytes, image_data: bytes) -> dict:
    test_pic = _clone_pic(pic, image_data)
    decoded = picture_to_rgba(test_pic, unswizzle_clut=True, force_opaque=False)

    total = len(rgba) // 4
    diff_pixels = 0
    max_d = 0
    for i in range(total):
        if rgba[i * 4 : i * 4 + 4] != decoded[i * 4 : i * 4 + 4]:
            diff_pixels += 1
            dr = abs(rgba[i * 4] - decoded[i * 4])
            dg = abs(rgba[i * 4 + 1] - decoded[i * 4 + 1])
            db = abs(rgba[i * 4 + 2] - decoded[i * 4 + 2])
            da = abs(rgba[i * 4 + 3] - decoded[i * 4 + 3])
            max_d = max(max_d, dr, dg, db, da)

    return {
        "total_pixels": total,
        "diff_pixels": diff_pixels,
        "pct_diff": round(100.0 * diff_pixels / total, 2) if total else 0.0,
        "max_per_channel_diff": max_d,
        "identical": diff_pixels == 0,
    }

def export_reference_palette(pic: Tim2Picture, out_path: str | Path) -> None:
    palette = decode_clut_rgba32(pic, unswizzle=True, force_opaque=False)
    n_colors = pic.clut_color_count or (16 if pic.image_type in (3, 4) else 256)
    palette = palette[:n_colors]
    cols = 16
    rows = (n_colors + cols - 1) // cols
    swatch_w, swatch_h = 32, 32
    w, h = cols * swatch_w, rows * swatch_h
    rgba = bytearray(w * h * 4)
    for idx, (r, g, b, a) in enumerate(palette):
        cx = (idx % cols) * swatch_w
        cy = (idx // cols) * swatch_h
        for dy in range(swatch_h):
            for dx in range(swatch_w):
                off = ((cy + dy) * w + cx + dx) * 4
                rgba[off] = r
                rgba[off + 1] = g
                rgba[off + 2] = b
                rgba[off + 3] = a
    write_png_rgba(out_path, w, h, bytes(rgba))

def _load_pic_from_databin(
    data_bin_path: Path, file_id: int, tim2_index: int, picture_index: int
) -> Tuple[Tim2Picture, bytes]:
    from datafat import read_entries  # type: ignore
    from lz77 import decompress  # type: ignore

    rows = read_entries(data_bin_path)
    row = next((r for r in rows if r["id"] == file_id and r["is_file"]), None)
    if row is None:
        raise SystemExit(f"ID {file_id} no encontrado en {data_bin_path}")

    with data_bin_path.open("rb") as fh:
        fh.seek(row["off"])
        raw = fh.read(row["size"])
    blob = decompress(raw, strict=False) if raw[:4] == b"LZ77" else raw

    tm2s = find_tim2_files(blob)
    if tim2_index >= len(tm2s):
        raise SystemExit(f"Archivo {file_id} tiene {len(tm2s)} TIM2, no hay indice {tim2_index}")
    tm2 = tm2s[tim2_index]
    if picture_index >= len(tm2.pictures):
        raise SystemExit(f"TIM2 {tim2_index} tiene {len(tm2.pictures)} pictures, no hay indice {picture_index}")
    pic = tm2.pictures[picture_index]
    return pic, blob


def _cmd_encode(args: argparse.Namespace) -> int:
    png_path = Path(args.png)
    if not png_path.exists():
        print(f"No existe: {png_path}", file=sys.stderr)
        return 1

    width, height, rgba = read_png_rgba(png_path)
    print(f"PNG: {width}x{height} ({len(rgba)} bytes RGBA)")

    data_bin = Path(args.data_bin)
    pic, _ = _load_pic_from_databin(data_bin, args.file_id, args.tim2_index or 0, args.picture_index or 0)
    print(f"Picture original: {pic.width}x{pic.height} {pic.image_type_name} image_size={pic.image_size} clut_size={pic.clut_size}")

    image_data, clut_data = encode_picture(pic, rgba, mode="preserve_palette")
    print(f"Encoded: image_data={len(image_data)} bytes (orig={pic.image_size})")

    if clut_data:
        print(f"  clut_data={len(clut_data)} bytes")
    else:
        print("  preservando CLUT original")

    rt = validate_round_trip(pic, rgba, image_data)
    if rt["identical"]:
        print("Round-trip: OK (identico)")
    else:
        print(f"Round-trip: {rt['diff_pixels']}/{rt['total_pixels']} pixeles distintos "
              f"({rt['pct_diff']}%), max_diff={rt['max_per_channel_diff']}")

    if args.out_png:
        test_pic = _clone_pic(pic, image_data, clut_data)
        decoded = picture_to_rgba(test_pic, unswizzle_clut=True, force_opaque=False)
        write_png_rgba(args.out_png, pic.width, pic.height, decoded)
        print(f"Round-trip PNG: {args.out_png}")

    if args.palette:
        pal_path = Path(args.palette)
        export_reference_palette(pic, pal_path)
        print(f"Paleta de referencia: {pal_path}")

    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Codificador PNG -> TIM2 (modo preserve_palette)",
    )
    sub = ap.add_subparsers(dest="cmd")

    enc = sub.add_parser("encode", help="Codificar un PNG y validar round-trip")
    enc.add_argument("png", help="Archivo PNG RGBA a codificar")
    enc.add_argument("data_bin", help="Ruta a originales/Data.bin")
    enc.add_argument("file_id", type=int, help="ID del archivo en Data.bin (ej. 9001)")
    enc.add_argument("tim2_index", nargs="?", type=int, default=0, help="Indice de TIM2 dentro del archivo (default 0)")
    enc.add_argument("picture_index", nargs="?", type=int, default=0, help="Indice de picture dentro del TIM2 (default 0)")
    enc.add_argument("--out-png", help="Escribe PNG del round-trip para inspeccion visual")
    enc.add_argument("--palette", help="Exporta paleta de referencia como PNG")

    ref = sub.add_parser("palette", help="Exportar paleta de referencia")
    ref.add_argument("data_bin", help="Ruta a originales/Data.bin")
    ref.add_argument("file_id", type=int)
    ref.add_argument("tim2_index", nargs="?", type=int, default=0)
    ref.add_argument("picture_index", nargs="?", type=int, default=0)
    ref.add_argument("--out", default="palette_ref.png", help="PNG de salida")

    args = ap.parse_args(argv)

    if args.cmd == "encode":
        return _cmd_encode(args)
    if args.cmd == "palette":
        data_bin = Path(args.data_bin)
        pic, _ = _load_pic_from_databin(data_bin, args.file_id, args.tim2_index or 0, args.picture_index or 0)
        export_reference_palette(pic, args.out)
        print(f"Paleta: {args.out}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

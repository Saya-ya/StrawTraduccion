"""Conversión TIM2 -> PNG sin dependencias externas.

Soporta lo necesario para los TIM2 observados en Strawberry Panic!:
- INDEX8 (image_type=5) con CLUT RGBA32.
- INDEX4 (image_type=4 o 3) con CLUT RGBA32.
- RGBA32/RGB24/RGB5A1 básicos para análisis si aparecen.

Incluye un escritor PNG RGBA puro (zlib), así no dependemos de Pillow para la
fase inicial. Si luego se instala Pillow, estos bytes RGBA también pueden pasarse
a PIL.Image.frombytes().
"""

from __future__ import annotations

from pathlib import Path
import argparse
import binascii
import json
import struct
import sys
import zlib
from typing import Iterable, List, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from tim2_parser import Tim2Picture, find_tim2_files, parse_tim2

RGBA = Tuple[int, int, int, int]


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png_rgba(path: str | Path, width: int, height: int, rgba: bytes, *, force_rgba: bool = False) -> None:
    if len(rgba) != width * height * 4:
        raise ValueError(f"RGBA len inválido: {len(rgba)} != {width}*{height}*4")

    try:
        from PIL import Image
        img = Image.frombytes("RGBA", (width, height), rgba)

        if not force_rgba:
            alpha = img.getchannel("A")
            if alpha.getextrema() == (255, 255):
                img = img.convert("RGB")

        img.save(path, format="PNG")
        return
    except ImportError:
        pass

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0: None
        raw.extend(rgba[y * stride:(y + 1) * stride])
    payload = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", payload) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _png_chunk(b"IEND", b"")
    Path(path).write_bytes(png)



def ps2_alpha_to_png(alpha: int, *, force_opaque: bool = False) -> int:
    if force_opaque:
        return 255
    if alpha <= 0:
        return 0
    return min(255, alpha * 2)


def rgba5551_to_rgba(px: int, *, force_opaque: bool = False) -> RGBA:
    # Convención común PS2: bits 0-4 R, 5-9 G, 10-14 B, bit 15 alpha.
    r5 = px & 0x1F
    g5 = (px >> 5) & 0x1F
    b5 = (px >> 10) & 0x1F
    a1 = (px >> 15) & 0x01
    r = (r5 << 3) | (r5 >> 2)
    g = (g5 << 3) | (g5 >> 2)
    b = (b5 << 3) | (b5 >> 2)
    a = 255 if (a1 or force_opaque) else 0
    return r, g, b, a


def unswizzle_psmt8_clut_index(i: int) -> int:
    return (i & ~0x18) | ((i & 0x08) << 1) | ((i & 0x10) >> 1)


def decode_clut_rgba32(pic: Tim2Picture, *, unswizzle: bool = True, force_opaque: bool = False) -> List[RGBA]:
    colors = pic.clut_color_count or (16 if pic.image_type in (3, 4) else 256)
    colors = min(colors, len(pic.clut_data) // 4)
    raw: List[RGBA] = []
    for i in range(colors):
        r, g, b, a = pic.clut_data[i * 4:i * 4 + 4]
        raw.append((r, g, b, ps2_alpha_to_png(a, force_opaque=force_opaque)))

    if unswizzle and colors >= 256 and pic.image_type == 5:
        linear: List[RGBA] = []
        for i in range(colors):
            j = unswizzle_psmt8_clut_index(i) if i < 256 else i
            linear.append(raw[j] if j < len(raw) else (0, 0, 0, 0))
        return linear
    return raw


def indexed8_to_rgba(pic: Tim2Picture, *, unswizzle_clut: bool = True, force_opaque: bool = False) -> bytes:
    palette = decode_clut_rgba32(pic, unswizzle=unswizzle_clut, force_opaque=force_opaque)
    missing = b"\xff\x00\xff\xff"
    tbl = [bytes(palette[i]) if i < len(palette) else missing for i in range(256)]
    data = pic.image_data[:pic.width * pic.height]
    return b"".join(map(tbl.__getitem__, data))


def indexed4_to_rgba(pic: Tim2Picture, *, force_opaque: bool = False) -> bytes:
    palette = decode_clut_rgba32(pic, unswizzle=False, force_opaque=force_opaque)
    missing = b"\xff\x00\xff\xff"
    tbl16 = [bytes(palette[i]) if i < len(palette) else missing for i in range(16)]
    byte_tbl = [tbl16[b & 0x0F] + tbl16[(b >> 4) & 0x0F] for b in range(256)]
    rgba = b"".join(map(byte_tbl.__getitem__, pic.image_data))
    return rgba[:pic.width * pic.height * 4]


def rgba32_to_rgba(pic: Tim2Picture, *, force_opaque: bool = False) -> bytes:
    out = bytearray()
    n = pic.width * pic.height
    for i in range(n):
        base = i * 4
        if base + 4 > len(pic.image_data):
            out.extend((255, 0, 255, 255))
            continue
        r, g, b, a = pic.image_data[base:base + 4]
        out.extend((r, g, b, ps2_alpha_to_png(a, force_opaque=force_opaque)))
    return bytes(out)


def rgb24_to_rgba(pic: Tim2Picture, *, force_opaque: bool = False) -> bytes:
    out = bytearray()
    n = pic.width * pic.height
    for i in range(n):
        base = i * 3
        if base + 3 > len(pic.image_data):
            out.extend((255, 0, 255, 255))
            continue
        r, g, b = pic.image_data[base:base + 3]
        out.extend((r, g, b, 255))
    return bytes(out)


def rgba5551_to_rgba_bytes(pic: Tim2Picture, *, force_opaque: bool = False) -> bytes:
    out = bytearray()
    n = pic.width * pic.height
    for i in range(n):
        base = i * 2
        if base + 2 > len(pic.image_data):
            out.extend((255, 0, 255, 255))
            continue
        px = struct.unpack_from("<H", pic.image_data, base)[0]
        out.extend(rgba5551_to_rgba(px, force_opaque=force_opaque))
    return bytes(out)


def parse_rgb_hex(value: str | None) -> tuple[int, int, int] | None:
    if value is None or value == "":
        return None
    v = value.strip().lstrip("#")
    if len(v) != 6:
        raise ValueError("El color debe ser RRGGBB, por ejemplo 000000")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def composite_rgba_over_background(rgba: bytes, background: tuple[int, int, int]) -> bytes:
    br, bg, bb = background
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        inv = 255 - a
        out[i] = (r * a + br * inv + 127) // 255
        out[i + 1] = (g * a + bg * inv + 127) // 255
        out[i + 2] = (b * a + bb * inv + 127) // 255
        out[i + 3] = 255
    return bytes(out)


def picture_to_rgba(pic: Tim2Picture, *, unswizzle_clut: bool = True, force_opaque: bool = False) -> bytes:
    if pic.image_type == 5:
        return indexed8_to_rgba(pic, unswizzle_clut=unswizzle_clut, force_opaque=force_opaque)
    if pic.image_type in (3, 4):
        return indexed4_to_rgba(pic, force_opaque=force_opaque)
    if pic.image_type == 0:
        return rgba32_to_rgba(pic, force_opaque=force_opaque)
    if pic.image_type == 1:
        return rgb24_to_rgba(pic, force_opaque=force_opaque)
    if pic.image_type == 2:
        return rgba5551_to_rgba_bytes(pic, force_opaque=force_opaque)
    raise NotImplementedError(f"image_type no soportado: {pic.image_type}")


def save_picture_png(
    pic: Tim2Picture,
    path: str | Path,
    *,
    unswizzle_clut: bool = True,
    force_opaque: bool = False,
    background: tuple[int, int, int] | None = None,
) -> None:
    rgba = picture_to_rgba(pic, unswizzle_clut=unswizzle_clut, force_opaque=(force_opaque and background is None))
    if background is not None:
        rgba = composite_rgba_over_background(rgba, background)
    write_png_rgba(path, pic.width, pic.height, rgba)


def export_tim2_pngs(
    data: bytes,
    out_dir: str | Path,
    *,
    prefix: str = "",
    force_opaque: bool = False,
    unswizzle_clut: bool = True,
    background: tuple[int, int, int] | None = None,
) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tm2s = find_tim2_files(data)
    results: list[dict] = []
    for t_index, tm2 in enumerate(tm2s):
        for pic in tm2.pictures:
            name = f"{prefix}{t_index:03d}_{pic.index:02d}.png" if len(tm2s) > 1 else f"{prefix}{pic.index:03d}.png"
            png_path = out / name
            save_picture_png(pic, png_path, unswizzle_clut=unswizzle_clut, force_opaque=force_opaque, background=background)
            info = pic.summary()
            info["png"] = str(png_path)
            info["container_index"] = t_index
            results.append(info)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convierte TIM2 suelto o blob con TIM2 a PNG(s)")
    ap.add_argument("input", help="Archivo .tm2 o blob que contenga TIM2")
    ap.add_argument("--out", default="dev/output/tim2_png", help="Directorio de salida")
    ap.add_argument("--prefix", default="", help="Prefijo para nombres PNG")
    ap.add_argument("--opaque", action="store_true", help="Ignora alpha PS2 y fuerza PNG opaco (útil para previsualizar)")
    ap.add_argument("--bg", "--background", dest="background", help="Compone alpha sobre fondo RRGGBB (ej. 000000 para fuentes blancas)")
    ap.add_argument("--no-clut-unswizzle", action="store_true", help="No reordena CLUT de 256 colores")
    args = ap.parse_args(argv)

    data = Path(args.input).read_bytes()
    results = export_tim2_pngs(
        data,
        args.out,
        prefix=args.prefix,
        force_opaque=args.opaque,
        unswizzle_clut=not args.no_clut_unswizzle,
        background=parse_rgb_hex(args.background),
    )
    meta_path = Path(args.out) / f"{args.prefix}metadata.json"
    meta_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"TIM2 exportados: {len(results)} -> {args.out}")
    for r in results:
        print(f"  {r['png']}  {r['width']}x{r['height']} {r['image_type_name']} clut={r['clut_size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

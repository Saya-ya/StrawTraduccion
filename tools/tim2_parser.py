"""Parser mínimo de TIM2 (PS2) para Strawberry Panic!/Data.bin.

El objetivo de este módulo es ser conservador y útil para análisis:
- Localiza TIM2 embebidos en blobs descomprimidos.
- Lee headers TIM2 + picture headers.
- Expone image_data y clut_data sin depender de Pillow.

Nota sobre layout observado en este juego:
    [TIM2 header 16 bytes]
    [picture header header_size bytes]
    [image_data image_size bytes]
    [clut_data clut_size bytes]

En TIM2, el campo total_size de cada picture NO incluye los 16 bytes del header
principal TIM2; sí incluye picture header + image + CLUT.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable, Iterator, List, Optional

TIM2_MAGIC = b"TIM2"
TIM2_HEADER_SIZE = 16
PICTURE_HEADER_MIN_SIZE = 48

# Valores observados/esperados para el campo image_type del picture header.
IMAGE_TYPE_NAMES = {
    0: "PSMCT32/RGBA32?",
    1: "PSMCT24/RGB24?",
    2: "PSMCT16/RGB5A1?",
    3: "INDEX4?",
    4: "INDEX4",
    5: "INDEX8",
}


@dataclass(frozen=True)
class Tim2Header:
    offset: int
    version: int
    format_id: int
    num_pictures: int
    reserved: bytes


@dataclass(frozen=True)
class Tim2Picture:
    index: int
    tim2_offset: int
    picture_offset: int
    total_size: int
    clut_size: int
    image_size: int
    header_size: int
    clut_color_count: int
    picture_format: int
    mipmap_count: int
    clut_type: int
    image_type: int
    width: int
    height: int
    gs_tex0: int
    gs_tex1: int
    gs_regs: bytes
    extra_header: bytes
    image_data: bytes
    clut_data: bytes

    @property
    def image_type_name(self) -> str:
        return IMAGE_TYPE_NAMES.get(self.image_type, f"UNKNOWN({self.image_type})")

    @property
    def bytes_per_pixel_numerator(self) -> int:
        """Numerador de bytes/pixel (use denominator=1 except INDEX4)."""
        if self.image_type in (3, 4):
            return 1  # 1 byte = 2 pixels
        if self.image_type == 5:
            return 1
        if self.image_type == 2:
            return 2
        if self.image_type == 1:
            return 3
        if self.image_type == 0:
            return 4
        return 0

    @property
    def expected_image_size(self) -> Optional[int]:
        pixels = self.width * self.height
        if self.image_type in (3, 4):
            return (pixels + 1) // 2
        if self.image_type == 5:
            return pixels
        if self.image_type == 2:
            return pixels * 2
        if self.image_type == 1:
            return pixels * 3
        if self.image_type == 0:
            return pixels * 4
        return None

    @property
    def is_size_consistent(self) -> bool:
        expected = self.expected_image_size
        return expected is None or expected == self.image_size

    def summary(self) -> dict:
        return {
            "index": self.index,
            "tim2_offset": self.tim2_offset,
            "picture_offset": self.picture_offset,
            "total_size": self.total_size,
            "header_size": self.header_size,
            "image_size": self.image_size,
            "clut_size": self.clut_size,
            "clut_color_count": self.clut_color_count,
            "picture_format": self.picture_format,
            "mipmap_count": self.mipmap_count,
            "clut_type": self.clut_type,
            "image_type": self.image_type,
            "image_type_name": self.image_type_name,
            "width": self.width,
            "height": self.height,
            "expected_image_size": self.expected_image_size,
            "size_consistent": self.is_size_consistent,
        }


@dataclass(frozen=True)
class Tim2File:
    header: Tim2Header
    pictures: List[Tim2Picture]

    @property
    def offset(self) -> int:
        return self.header.offset

    @property
    def size(self) -> int:
        return TIM2_HEADER_SIZE + sum(p.total_size for p in self.pictures)

    def summary(self) -> dict:
        return {
            "offset": self.offset,
            "version": self.header.version,
            "format_id": self.header.format_id,
            "num_pictures": self.header.num_pictures,
            "size": self.size,
            "pictures": [p.summary() for p in self.pictures],
        }


class Tim2ParseError(ValueError):
    pass


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def parse_tim2(data: bytes, offset: int = 0, *, strict: bool = True) -> Tim2File:
    if offset < 0 or offset + TIM2_HEADER_SIZE > len(data):
        raise Tim2ParseError("Offset fuera de rango para header TIM2")
    if data[offset:offset + 4] != TIM2_MAGIC:
        raise Tim2ParseError(f"Magic TIM2 no encontrado en 0x{offset:X}")

    version = data[offset + 4]
    format_id = data[offset + 5]
    num_pictures = _u16(data, offset + 6)
    reserved = data[offset + 8:offset + 16]

    if strict:
        if version not in (3, 4):
            raise Tim2ParseError(f"Version TIM2 inesperada: {version}")
        if num_pictures <= 0 or num_pictures > 256:
            raise Tim2ParseError(f"num_pictures inválido: {num_pictures}")

    header = Tim2Header(offset, version, format_id, num_pictures, reserved)
    pictures: List[Tim2Picture] = []
    pos = offset + TIM2_HEADER_SIZE

    for pic_index in range(num_pictures):
        if pos + PICTURE_HEADER_MIN_SIZE > len(data):
            raise Tim2ParseError("Picture header truncado")

        total_size = _u32(data, pos + 0x00)
        clut_size = _u32(data, pos + 0x04)
        image_size = _u32(data, pos + 0x08)
        header_size = _u16(data, pos + 0x0C)
        clut_color_count = _u16(data, pos + 0x0E)
        picture_format = data[pos + 0x10]
        mipmap_count = data[pos + 0x11]
        clut_type = data[pos + 0x12]
        image_type = data[pos + 0x13]
        width = _u16(data, pos + 0x14)
        height = _u16(data, pos + 0x16)
        gs_tex0 = _u64(data, pos + 0x18)
        gs_tex1 = _u64(data, pos + 0x20)
        gs_regs = data[pos + 0x28:pos + 0x30]

        if strict:
            if header_size < PICTURE_HEADER_MIN_SIZE:
                raise Tim2ParseError(f"header_size inválido: {header_size}")
            if total_size < header_size:
                raise Tim2ParseError("total_size menor que header_size")
            if total_size != header_size + image_size + clut_size:
                raise Tim2ParseError(
                    "total_size no coincide con header+image+clut: "
                    f"{total_size} != {header_size}+{image_size}+{clut_size}"
                )
            if width <= 0 or height <= 0 or width > 8192 or height > 8192:
                raise Tim2ParseError(f"dimensiones inválidas: {width}x{height}")
            if pos + total_size > len(data):
                raise Tim2ParseError("Picture data truncada")
            if image_type in (3, 4, 5) and clut_size <= 0:
                raise Tim2ParseError("Textura indexada sin CLUT")
            expected = None
            pixels = width * height
            if image_type in (3, 4):
                expected = (pixels + 1) // 2
            elif image_type == 5:
                expected = pixels
            elif image_type == 2:
                expected = pixels * 2
            elif image_type == 1:
                expected = pixels * 3
            elif image_type == 0:
                expected = pixels * 4
            if expected is not None and expected != image_size:
                raise Tim2ParseError(
                    f"image_size inesperado para tipo {image_type}: "
                    f"{image_size} != {expected} ({width}x{height})"
                )

        extra_header = data[pos + PICTURE_HEADER_MIN_SIZE:pos + header_size]
        image_start = pos + header_size
        image_end = image_start + image_size
        clut_end = image_end + clut_size
        image_data = data[image_start:image_end]
        clut_data = data[image_end:clut_end]

        pictures.append(Tim2Picture(
            index=pic_index,
            tim2_offset=offset,
            picture_offset=pos,
            total_size=total_size,
            clut_size=clut_size,
            image_size=image_size,
            header_size=header_size,
            clut_color_count=clut_color_count,
            picture_format=picture_format,
            mipmap_count=mipmap_count,
            clut_type=clut_type,
            image_type=image_type,
            width=width,
            height=height,
            gs_tex0=gs_tex0,
            gs_tex1=gs_tex1,
            gs_regs=gs_regs,
            extra_header=extra_header,
            image_data=image_data,
            clut_data=clut_data,
        ))
        pos += total_size

    return Tim2File(header, pictures)


def iter_tim2_offsets(data: bytes) -> Iterator[int]:
    pos = 0
    while True:
        off = data.find(TIM2_MAGIC, pos)
        if off < 0:
            return
        yield off
        pos = off + 4


def find_tim2_files(data: bytes, *, strict: bool = True) -> List[Tim2File]:
    found: List[Tim2File] = []
    for off in iter_tim2_offsets(data):
        try:
            tm2 = parse_tim2(data, off, strict=strict)
        except Tim2ParseError:
            continue
        found.append(tm2)
    return found


def describe_tim2(data: bytes, offset: int = 0) -> str:
    tm2 = parse_tim2(data, offset)
    lines = [
        f"TIM2 @ 0x{tm2.offset:X}: version={tm2.header.version} "
        f"format={tm2.header.format_id} pictures={len(tm2.pictures)} size={tm2.size:,}"
    ]
    for p in tm2.pictures:
        ok = "OK" if p.is_size_consistent else "WARN"
        lines.append(
            f"  pic {p.index}: {p.width}x{p.height} {p.image_type_name} "
            f"image={p.image_size:,} clut={p.clut_size:,} colors={p.clut_color_count} "
            f"ct={p.clut_type} pf={p.picture_format} mip={p.mipmap_count} [{ok}]"
        )
    return "\n".join(lines)


__all__ = [
    "TIM2_MAGIC",
    "Tim2Header",
    "Tim2Picture",
    "Tim2File",
    "Tim2ParseError",
    "parse_tim2",
    "find_tim2_files",
    "iter_tim2_offsets",
    "describe_tim2",
]

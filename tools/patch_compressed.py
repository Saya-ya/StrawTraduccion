#!/usr/bin/env python3
"""
patch_compressed.py — Parchea texto directamente en el stream comprimido LZ77
sin necesidad de recomprimir. Solo funciona para bytes que son LITERAL.

Estrategia:
  1. Trazar la descompresión para mapear cada byte de salida a su posición
     en el stream comprimido (LIT = posición directa, MATCH = referencia)
  2. Para bytes LITERAL, modificar el compressed[comp_pos] directamente
  3. Para bytes MATCH, el cambio debe hacerse en la fuente del match

Uso:
    python patch_compressed.py <id_archivo> <offset_dec> <bytes_nuevos>
    python patch_compressed.py 7461 0x1AB5 "奧深園のくに"
"""

import struct
import sys
from pathlib import Path

from lz77 import decompress
from datafat import read_entries, find_row


def trace_decompression(comp_data, expected_size):
    """
    Traza la descompresión y devuelve:
    - output: bytes descomprimidos
    - mapping: lista de (type, comp_offset, ...) por cada byte de salida
      type='LIT': (comp_offset, value)
      type='MATCH': (comp_offset, window_offset, sub_index)
    """
    out = bytearray()
    window = bytearray([0x00] * 4096)  # PS2 usa memset(buf, 0, 4113)
    window_pos = 0xFEE
    src_pos = 0
    mapping = []

    while src_pos < len(comp_data) and len(out) < expected_size:
        flags = comp_data[src_pos]
        src_pos += 1

        for bit in range(8):
            if len(out) >= expected_size:
                break
            if src_pos >= len(comp_data):
                break

            is_literal = (flags & (1 << bit)) != 0

            if is_literal:
                val = comp_data[src_pos]
                mapping.append(('LIT', src_pos))
                src_pos += 1
                out.append(val)
                window[window_pos] = val
                window_pos = (window_pos + 1) & 0xFFF
            else:
                b1, b2 = comp_data[src_pos], comp_data[src_pos + 1]
                match_src = src_pos
                src_pos += 2
                offset = b1 | ((b2 & 0xF0) << 4)
                length = (b2 & 0x0F) + 3

                for k in range(length):
                    if len(out) >= expected_size:
                        break
                    val = window[offset]
                    mapping.append(('MATCH', match_src, offset, k))
                    out.append(val)
                    window[window_pos] = val
                    window_pos = (window_pos + 1) & 0xFFF
                    offset = (offset + 1) & 0xFFF

    return bytes(out), mapping


def patch_file(data_bin_path, file_id, dec_offset, new_bytes):
    """
    Parchea bytes en el stream comprimido de un archivo LZ77.
    Modifica Data.bin in-place sin cargarlo completo en RAM.
    """
    bin_path = Path(data_bin_path)
    file_size = bin_path.stat().st_size

    rows = read_entries(bin_path)
    row = find_row(rows, file_id)
    if row is None:
        return False, f"ID {file_id} no encontrado en FAT", []
    data_offset = row['off']
    orig_size = row['size']  # tamaño REAL: size_field de la fila siguiente

    # Leer solo los bytes del archivo comprimido
    with open(bin_path, 'rb') as f:
        f.seek(data_offset)
        raw = f.read(orig_size)

    hdr = raw[:4]
    if hdr != b'LZ77':
        return False, f"ID {file_id} no es LZ77", []

    expected_size = struct.unpack_from('<I', raw, 4)[0]
    comp_data = raw[12:]  # header es 12 bytes (magic + decomp_size + comp_size)

    # Trazar descompresión para encontrar las posiciones en el stream comprimido
    out, mapping = trace_decompression(comp_data, expected_size)

    if dec_offset + len(new_bytes) > len(out):
        return False, f"Offset {dec_offset}+{len(new_bytes)} fuera de rango ({len(out)} bytes)", []

    # Verificar que todos los bytes son LITERAL
    for i in range(dec_offset, dec_offset + len(new_bytes)):
        if mapping[i][0] != 'LIT':
            return False, f"Byte {i} (0x{i:04X}) es MATCH. No se puede parchear directamente.", []

    # Aplicar cambios in-place (solo los bytes necesarios)
    comp_offsets = []
    with open(bin_path, 'r+b') as f:
        for i, new_byte in enumerate(new_bytes):
            dec_i = dec_offset + i
            comp_pos = mapping[dec_i][1]
            comp_offsets.append(comp_pos)
            # comp_pos está mapeado contra raw[12:], por tanto el stream empieza
            # en data_offset + 12. Usar +16 era el bug del header viejo.
            abs_pos = data_offset + 12 + comp_pos
            f.seek(abs_pos)
            f.write(bytes([new_byte]))

    return True, f"Parcheados {len(new_bytes)} bytes en {len(comp_offsets)} posiciones", comp_offsets


def main():
    if len(sys.argv) < 4:
        print("Uso: python patch_compressed.py <file_id> <dec_offset_hex> <new_text>")
        print("  python patch_compressed.py 7461 0x1AB5 '奧深園のくに'")
        sys.exit(1)

    file_id = int(sys.argv[1])
    dec_offset = int(sys.argv[2], 16)
    new_text = sys.argv[3]

    # Convertir a UTF-16LE (encoding del script)
    new_bytes = new_text.encode('utf-16-le')

    # También hay que añadir \r (0x0D 0x00) al final si el original lo tiene
    # Por simplicidad, el usuario debe incluir el \r si es necesario

    data_bin_path = "work/Data_patched.bin"

    # Trabajar sobre una copia (si no existe ya)
    import shutil
    src = "originales/Data.bin"
    if not Path(data_bin_path).exists():
        print(f"Copiando {src} -> {data_bin_path} (solo primera vez)...")
        shutil.copy2(src, data_bin_path)

    success, msg, comp_offsets = patch_file(data_bin_path, file_id, dec_offset, new_bytes)

    if success:
        print(f"[OK] {msg}")
        print(f"  Compressed offsets: {comp_offsets}")
        print(f"\n  Data.bin modificado: {data_bin_path}")
        print(f"  Para probar: reconstruir ISO con este Data.bin")
    else:
        print(f"[FAIL] {msg}")


if __name__ == "__main__":
    main()

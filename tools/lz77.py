#!/usr/bin/env python3
"""
lz77.py — Descompresor LZSS de PS2 (verificado contra MIPS en SLPS_256.11).

Formato del header (16 bytes):
  [magic "LZ77"(4)] [decomp_size:u32] [comp_size_con_header:u32] [metadata:u32]

Algoritmo (confirmado por desensamblado del ELF en 0x3B500):
  - Ventana: 4096 bytes, inicio: 0xFEE
  - Flags: LSB-first (bit 0 primero)
  - Bit=1 → LITERAL (1 byte)
  - Bit=0 → MATCH (2 bytes):
      offset = b1 | ((b2 & 0xF0) << 4)   (12 bits, absoluto en ventana)
      length = (b2 & 0x0F) + 3           (3-18 bytes)

ADVERTENCIA: El compresor actual produce streams válidos en Python
pero causa PANTALLA NEGRA en PS2 real (ver PROYECTO_TRADUCCION.md).
Para Fase 3 (prueba de concepto), NO recomprimas — modifica bytes
directamente en el stream descomprimido.
"""

import struct

MAGIC = b"LZ77"
WINDOW_SIZE = 4096
WINDOW_START = 0xFEE
MIN_MATCH = 3
MAX_MATCH = 18


def decompress(compressed_data, expected_size=None):
    """
    Descomprime datos en formato LZSS de PS2.
    Si expected_size es None, lo lee del header.
    """
    data = compressed_data
    pos = 0

    # Verificar/parsear header de 16 bytes
    if data[:4] == MAGIC:
        expected_size = struct.unpack_from("<I", data, 4)[0]
        # Saltar header de 16 bytes
        data = data[16:]
    elif expected_size is None:
        raise ValueError("Se requiere expected_size o header LZ77 de 16 bytes")

    out = bytearray()
    window = bytearray(WINDOW_SIZE)
    window_pos = WINDOW_START

    while pos < len(data) and len(out) < expected_size:
        flags = data[pos]
        pos += 1

        for bit in range(8):
            if len(out) >= expected_size:
                break

            # LSB-first: bit 0 primero
            is_literal = (flags & (1 << bit)) != 0

            if is_literal:
                if pos >= len(data):
                    break
                val = data[pos]
                pos += 1
                out.append(val)
                window[window_pos] = val
                window_pos = (window_pos + 1) & 0xFFF
            else:
                if pos + 1 >= len(data):
                    break
                b1 = data[pos]
                b2 = data[pos + 1]
                pos += 2

                offset = b1 | ((b2 & 0xF0) << 4)
                length = (b2 & 0x0F) + 3

                for _ in range(length):
                    if len(out) >= expected_size:
                        break
                    val = window[offset]
                    out.append(val)
                    window[window_pos] = val
                    window_pos = (window_pos + 1) & 0xFFF
                    offset = (offset + 1) & 0xFFF

    return bytes(out)


def compress(uncompressed_data):
    """
    Comprime usando LZSS de PS2.
    ATENCIÓN: Este compresor produce streams que funcionan en Python
    pero CAUSAN PANTALLA NEGRA en hardware real. Usar solo para
    round-trip tests en Python, NO para inyectar en ISO.
    """
    data_len = len(uncompressed_data)
    compressed = bytearray()

    window = bytearray(WINDOW_SIZE)
    window_pos = WINDOW_START

    src_pos = 0
    block_flags = 0
    bit_count = 0
    block_tokens = bytearray()

    def flush_block():
        nonlocal block_flags, bit_count, block_tokens
        if bit_count > 0:
            compressed.append(block_flags)
            compressed.extend(block_tokens)
            block_flags = 0
            bit_count = 0
            block_tokens = bytearray()

    while src_pos < data_len:
        match_len = 0
        match_offset = 0
        max_len = min(MAX_MATCH, data_len - src_pos)

        if max_len >= MIN_MATCH:
            for w_idx in range(WINDOW_SIZE):
                dist = (window_pos - w_idx) & 0xFFF
                if dist == 0:
                    continue

                curr_len = 0
                while curr_len < max_len:
                    if curr_len < dist:
                        wpos = (w_idx + curr_len) & 0xFFF
                        val = window[wpos]
                    else:
                        val = uncompressed_data[src_pos + curr_len - dist]

                    if val != uncompressed_data[src_pos + curr_len]:
                        break
                    curr_len += 1

                if curr_len > match_len:
                    match_len = curr_len
                    match_offset = w_idx
                    if match_len == max_len:
                        break

        if match_len < MIN_MATCH:
            match_len = 1
            val = uncompressed_data[src_pos]
            block_flags |= (1 << bit_count)  # bit=1 → literal
            block_tokens.append(val)
        else:
            b1 = match_offset & 0xFF
            b2 = ((match_offset >> 4) & 0xF0) | ((match_len - 3) & 0x0F)
            block_tokens.extend([b1, b2])

        for k in range(match_len):
            window[window_pos] = uncompressed_data[src_pos + k]
            window_pos = (window_pos + 1) & 0xFFF

        src_pos += match_len
        bit_count += 1
        if bit_count == 8:
            flush_block()

    flush_block()

    # Construir header de 16 bytes
    comp_total = 16 + len(compressed)
    header = struct.pack("<4sIII", MAGIC, data_len, comp_total, 0)
    return header + bytes(compressed)


def decompress_file(input_path, output_path=None):
    """Descomprime un archivo LZ77 a disco."""
    from pathlib import Path
    data = Path(input_path).read_bytes()
    decomp = decompress(data)
    if output_path is None:
        output_path = str(input_path) + ".dec"
    Path(output_path).write_bytes(decomp)
    return decomp


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Uso: python lz77.py [d|decompress] <input> [output]")
        print("      python lz77.py [c|compress] <input> [output]  (solo para tests locales)")
        sys.exit(1)

    mode = sys.argv[1]
    input_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    if mode in ("d", "decompress"):
        result = decompress_file(input_path, output_path)
        print(f"Descomprimido: {len(result):,} bytes")
    elif mode in ("c", "compress"):
        result = compress_file(input_path, output_path)
        print(f"Comprimido: {len(result):,} bytes  [ADVERTENCIA: no usar para ISO]")
    else:
        print(f"Modo desconocido: {mode}")

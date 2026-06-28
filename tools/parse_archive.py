#!/usr/bin/env python3
"""
parse_archive.py — Analiza el índice de archivos de Data.bin.

Formato del archive:
  Header (20 bytes):
    0x00: magic     u32 LE = 0x7878C800
    0x04: num_files u32 LE  
    0x08: ???       u32 LE = 0x8000
    0x0C: ???       u32 LE = 0x60000
    0x10: ???       u32 LE = 3
  File table (FAT) en offset 0x8004:
    Cada entrada = 12 bytes: [id:u32, size:u32, offset:u32] LE
  Datos a partir de offset 0x60000

Uso:
    python parse_archive.py [--json index.json] [data.bin]
"""

import json
import struct
import sys
from collections import defaultdict, Counter
from pathlib import Path


HEADER_FORMAT = "<4I"
ENTRY_FORMAT = "<III"          # id, size, offset
TABLE_OFFSET = 0x8004
ENTRY_SIZE = 12
MAGIC = 0x7878C800

SIGNATURES = {
    b"LZ77": "LZ77 compressed",
    b"SShd": "SS2 ADPCM audio (Sony ADPCM)",
    b"TIM2": "TIM2 texture",
    b"\x00\x00\x00\x00": "zero/empty",
}


def detect_type(first_bytes):
    for sig, desc in SIGNATURES.items():
        if first_bytes.startswith(sig):
            return desc
    return "raw/unknown"


def parse_archive(path):
    data = path.read_bytes()
    file_size = len(data)

    if len(data) < 20:
        raise ValueError(f"Archivo demasiado pequeño: {file_size} bytes")

    magic, num_files, val2, val3 = struct.unpack_from(HEADER_FORMAT, data, 0)

    if magic != MAGIC:
        raise ValueError(f"Magic incorrecto: 0x{magic:08X} (esperado 0x{MAGIC:08X})")

    print(f"=== Cabecera de Data.bin ===")
    print(f"Magic:      0x{magic:08X}")
    print(f"num_files:  {num_files} (0x{num_files:08X})")
    print(f"val2:       {val2} (0x{val2:08X})")
    print(f"val3:       {val3} (0x{val3:08X})")
    print(f"File size:  {file_size:,} bytes ({file_size / 1024 / 1024:.1f} MB)")
    print(f"FAT offset: 0x{TABLE_OFFSET:08X}")
    print()

    expected_table_end = TABLE_OFFSET + num_files * ENTRY_SIZE
    print(f"Tabla: 0x{TABLE_OFFSET:X} .. 0x{expected_table_end:X} ({num_files * ENTRY_SIZE:,} bytes)")

    if expected_table_end > file_size:
        print(f"  [WARN] La tabla se extiende más allá del archivo!")

    print()

    # Parsear tabla
    entries = []
    file_types = defaultdict(int)
    gaps_by_id = []
    prev_entry = None
    alignment_issues = 0

    print(f"Parseando {num_files} entradas...")

    for i in range(num_files):
        entry_offset = TABLE_OFFSET + i * ENTRY_SIZE
        raw = data[entry_offset:entry_offset + ENTRY_SIZE]
        if len(raw) < ENTRY_SIZE:
            break
        fid, size, off = struct.unpack(ENTRY_FORMAT, raw)
        entry = {"index": i, "id": fid, "size": size, "offset": off}
        entries.append(entry)

        # Detectar tipo
        if off + 4 <= file_size:
            hdr = data[off:off + 4]
        else:
            hdr = b"\x00"
        ftype = detect_type(hdr)
        file_types[ftype] += 1

        # Gap detection entre archivos consecutivos en disco
        if prev_entry and off > 0 and prev_entry["offset"] > 0:
            prev_end = prev_entry["offset"] + prev_entry["size"]
            next_start = off
            if next_start > prev_end:
                gaps_by_id.append((prev_entry["id"], fid, next_start - prev_end))

        # Alineación
        if off > 0 and off % 0x800 != 0:
            alignment_issues += 1

        prev_entry = entry

    print(f"Entradas parseadas: {len(entries)}")
    print()

    # IDs
    ids = [e["id"] for e in entries]
    id_counts = Counter(ids)
    dupes = [(id_, c) for id_, c in id_counts.items() if c > 1]

    # Rangos de IDs por tipo
    type_id_ranges = defaultdict(list)
    for e in entries:
        if e["offset"] + 4 <= file_size:
            hdr = data[e["offset"]:e["offset"] + 4]
            ftype = detect_type(hdr)
            type_id_ranges[ftype].append(e["id"])

    # Estadísticas
    print(f"=== Tipos de archivo ===")
    for ftype, count in sorted(file_types.items(), key=lambda x: -x[1]):
        pct = count / len(entries) * 100
        print(f"  {ftype:<35s}: {count:>6d} ({pct:5.1f}%)")

    print()
    print(f"=== Integridad ===")
    print(f"Problemas de alineación: {alignment_issues}")
    print(f"Gaps entre archivos (por offset): {len(gaps_by_id)}")
    if gaps_by_id:
        print("Primeros 10 gaps:")
        for pid, nid, gap in gaps_by_id[:10]:
            print(f"  ID {pid} -> ID {nid}: gap de {gap:,} bytes")

    if dupes:
        print(f"\nIDs duplicados: {len(dupes)}")
        for id_, c in dupes[:10]:
            print(f"  ID {id_}: {c} ocurrencias")
    else:
        print(f"IDs: todos únicos ({len(set(ids))} en {len(ids)})")

    # Solapamientos (overlap): cuando offset_A + size_A > offset_B
    overlapped = 0
    for e in entries:
        e_end = e["offset"] + e["size"]
        for e2 in entries:
            if e2["offset"] > e["offset"] and e_end > e2["offset"]:
                overlapped += 1
                break

    print(f"\nArchivos con solapamiento (overlap): {overlapped}")
    if overlapped > 0:
        print("(Esto es normal — la FAT reporta tamaños mayores que los reales)")
        print("IDs con overlap:")
        shown = 0
        for e in entries:
            e_end = e["offset"] + e["size"]
            overlapped_with = []
            for e2 in entries:
                if e2["offset"] > e["offset"] and e_end > e2["offset"]:
                    overlapped_with.append(e2["id"])
            if overlapped_with and shown < 10:
                print(f"  ID {e['id']:>6d} (offset=0x{e['offset']:08X} size={e['size']:,}) solapa a: {overlapped_with[:5]}")
                shown += 1

    print()
    print(f"=== Distribución de IDs por tipo ===")
    for ftype, id_list in sorted(type_id_ranges.items(), key=lambda x: -len(x[1])):
        id_sorted = sorted(id_list)
        print(f"  {ftype}: IDs {id_sorted[0]} .. {id_sorted[-1]} ({len(id_list)} archivos)")

    # Rango de datos
    valid_offsets = [e["offset"] for e in entries if e["offset"] > 0]
    if valid_offsets:
        print(f"\nOffset mínimo de datos: 0x{min(valid_offsets):08X}")
        print(f"Offset máximo:          0x{max(valid_offsets):08X}")
        max_data_end = max(e["offset"] + e["size"] for e in entries if e["offset"] > 0)
        print(f"Fin de datos:            0x{max_data_end:08X}")
        print(f"Espacio usado:           {max_data_end - min(valid_offsets):,} bytes")

    return {
        "magic": magic,
        "num_files": num_files,
        "entries": entries,
        "file_types": dict(file_types),
        "dupe_ids": dupes,
        "overlaps": overlapped,
        "alignment_issues": alignment_issues,
    }


def main():
    args = sys.argv[1:]
    json_out = None
    bin_path = None

    i = 0
    while i < len(args):
        if args[i] == "--json" and i + 1 < len(args):
            json_out = args[i + 1]
            i += 2
        else:
            bin_path = args[i]
            i += 1

    if bin_path is None:
        candidates = [
            Path("originales/Data.bin"),
            Path("../originales/Data.bin"),
            Path("Data.bin"),
        ]
        for c in candidates:
            if c.exists():
                bin_path = c
                break
        if bin_path is None:
            print("Error: No se encontró Data.bin.")
            print("Uso: python parse_archive.py [--json index.json] [ruta/Data.bin]")
            sys.exit(1)

    bin_path = Path(bin_path)
    if not bin_path.exists():
        print(f"Error: {bin_path} no existe.")
        sys.exit(1)

    result = parse_archive(bin_path)

    if json_out:
        json_result = {
            "magic": result["magic"],
            "num_files": result["num_files"],
            "file_types": result["file_types"],
            "entries": result["entries"],
        }
        Path(json_out).write_text(json.dumps(json_result, indent=2), encoding="utf-8")
        print(f"\nÍndice exportado a: {json_out}")


if __name__ == "__main__":
    main()

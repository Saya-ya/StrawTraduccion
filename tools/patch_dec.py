#!/usr/bin/env python3
"""
patch_dec.py — Script Rebuilder para Strawberry Panic! (PS2)

Flujo completo:
  1. Lee el .dec modificado (o aplica traducciones del CSV)
  2. Recomprime con lz77.compress() (ventana inicializada con 0x00)
  3. Calcula el tamaño REAL del slot (distancia al siguiente offset en FAT)
  4. Inyecta dentro del slot sin corromper archivos vecinos

IMPORTANTE: El campo 'size_field' de la fila de un ID NO es el tamaño de ese
archivo: es el tamaño del archivo anterior. El tamaño real leído por el juego
está en el size_field de la fila SIGUIENTE. Ver tools/datafat.py.

Uso:
    python patch_dec.py --id 7461 --dec work/scripts_extraidos/ID_07461.dec
    python patch_dec.py --id 7461 --dec work/scripts_extraidos/ID_07461.dec --verify
"""

import struct
import sys
import shutil
import argparse
from pathlib import Path

from lz77 import decompress, compress
from datafat import (
    FAT_OFFSET,
    NUM_ENTRIES as FAT_ENTRIES,
    ENTRY_SIZE,
    read_entries,
    find_row,
    slot_capacity,
    size_field_write_offset,
)

DATA_BIN_ORIG = Path('originales/Data.bin')
DATA_BIN_WORK = Path('work/Data_patched.bin')
def load_fat(bin_path):
    """Lee la FAT completa y devuelve entradas canónicas de archivos reales."""
    rows = read_entries(bin_path)
    entries = [
        {
            'row': r['row'],
            'fid': r['id'],
            'size_field': r['size_field'],
            'size': r['size'],
            'foff': r['off'],
        }
        for r in rows if r['is_file']
    ]
    entries_by_offset = sorted(entries, key=lambda e: e['foff'])
    return entries, entries_by_offset, rows


def get_slot_info(target_fid, bin_path):
    """
    Devuelve (foff, real_slot_size) para un file ID.
    real_slot_size = distancia al siguiente entry en el archivo.
    """
    rows = read_entries(bin_path)

    # Encontrar el entry
    target = find_row(rows, target_fid)
    if target is None:
        return None, None

    return target['off'], slot_capacity(rows, target)


def get_file_info(target_fid, bin_path):
    """
    Devuelve (row, foff, file_size, slot_size) para un ID.
    file_size es el tamaño REAL que el loader lee (size_field de la fila siguiente).
    slot_size es la capacidad física hasta el siguiente offset.
    """
    rows = read_entries(bin_path)
    target = find_row(rows, target_fid)
    if target is None:
        return None, None, None, None
    return target, target['off'], target['size'], slot_capacity(rows, target)


def decompress_from_data_bin(target_fid, bin_path):
    """Descomprime el script de un file ID desde Data.bin."""
    _, foff, file_size, slot_size = get_file_info(target_fid, bin_path)
    if foff is None:
        raise ValueError(f"ID {target_fid} no encontrado en FAT")

    with open(bin_path, 'rb') as f:
        f.seek(foff)
        raw = f.read(file_size)

    if raw[:4] != b'LZ77':
        raise ValueError(f"ID {target_fid} en 0x{foff:08X} no es LZ77 (magic: {raw[:4].hex()})")

    return decompress(raw)


def inject_compressed(target_fid, comp_data, bin_path):
    """
    Inyecta datos comprimidos en el slot de un file ID.
    Solo escribe dentro del slot real (no corrompe vecinos).
    Actualiza el size_field en la FAT para que el PS2 lea los bytes correctos.
    """
    target, foff, old_file_size, slot_size = get_file_info(target_fid, bin_path)
    if foff is None:
        raise ValueError(f"ID {target_fid} no encontrado en FAT")

    if len(comp_data) > slot_size:
        raise ValueError(
            f"Datos ({len(comp_data):,} bytes) no caben en slot ({slot_size:,} bytes). "
            f"Necesita reubicación."
        )

    # La FAT necesita el tamaño REAL actualizado para que el PS2 lea la cantidad
    # correcta de bytes. En este archive, el tamaño del archivo de la fila i vive
    # en el size_field de la fila i+1 (NO en la fila actual).
    file_size = len(comp_data)  # incluye header LZ77 de 12 bytes

    with open(bin_path, 'r+b') as f:
        # Escribir datos comprimidos en el slot
        f.seek(foff)
        f.write(comp_data)
        # Rellenar el resto del slot con ceros
        f.write(b'\x00' * (slot_size - len(comp_data)))

        # Actualizar size_field de la FILA SIGUIENTE.
        f.seek(size_field_write_offset(target))
        f.write(struct.pack('<I', file_size))

    return foff, slot_size


def patch_script(target_fid, dec_path, bin_path, verify=False, verbose=True, all_literal=False):
    """
    Pipeline completo: leer .dec → recomprimir → inyectar.
    Si all_literal=True, usa 100% literales (sin matches).
    """
    if verbose:
        print(f"[*] Procesando ID {target_fid} desde {dec_path}")

    _, foff, file_size, slot_size = get_file_info(target_fid, bin_path)
    if foff is None:
        print(f"[!] ID {target_fid} no encontrado")
        return False

    if verbose:
        print(f"    Offset: 0x{foff:08X}, slot: {slot_size:,} bytes ({slot_size // 1024}KB)")

    # Leer .dec modificado
    with open(dec_path, 'rb') as f:
        dec_data = f.read()

    # Verificar round-trip antes de inyectar
    if verify:
        comp_test = compress(dec_data, all_literal=all_literal)
        redec_test = decompress(comp_test)
        diffs = sum(a != b for a, b in zip(dec_data, redec_test))
        if diffs > 0 or len(dec_data) != len(redec_test):
            print(f"[!] FALLO round-trip: {diffs} diffs, "
                  f"orig={len(dec_data)}, redec={len(redec_test)}")
            return False
        if verbose:
            print(f"    Round-trip: OK (0 diffs)")

    # Recomprimir
    comp_data = compress(dec_data, all_literal=all_literal)
    if verbose:
        our_decomp = struct.unpack_from('<I', comp_data, 4)[0]
        our_comp_size = struct.unpack_from('<I', comp_data, 8)[0]
        our_meta = struct.unpack_from('<I', comp_data, 12)[0]
        print(f"    Comprimido: {len(dec_data):,} → {len(comp_data):,} bytes "
              f"({len(comp_data) / slot_size * 100:.1f}% del slot)")
        print(f"    Header: decomp={our_decomp:,} comp={our_comp_size:,} meta=0x{our_meta:08X}")

    if len(comp_data) > slot_size:
        print(f"[!] No cabe en slot ({len(comp_data):,} > {slot_size:,}). "
              f"Necesita reubicación.")
        return False

    # Inyectar
    foff_written, _ = inject_compressed(target_fid, comp_data, bin_path)
    if verbose:
        print(f"    Inyectado en 0x{foff_written:08X} ✓")

    return True


def ensure_work_copy():
    """Crea la copia de trabajo si no existe o está desactualizada."""
    DATA_BIN_WORK.parent.mkdir(parents=True, exist_ok=True)
    if (not DATA_BIN_WORK.exists() or
            DATA_BIN_WORK.stat().st_size != DATA_BIN_ORIG.stat().st_size):
        print(f"Creando copia de trabajo ({DATA_BIN_ORIG.stat().st_size // 1024 // 1024} MB)...")
        shutil.copy2(DATA_BIN_ORIG, DATA_BIN_WORK)
        print("Copia lista.")


def main():
    parser = argparse.ArgumentParser(description='Script Rebuilder para Strawberry Panic!')
    parser.add_argument('--id',     type=int, required=True, help='File ID en FAT (ej: 7461)')
    parser.add_argument('--dec',    type=str, required=True, help='Ruta al .dec modificado')
    parser.add_argument('--out',    type=str, default=None,  help='Data.bin destino (default: work/Data_patched.bin)')
    parser.add_argument('--verify', action='store_true',     help='Verificar round-trip antes de inyectar')
    parser.add_argument('--all-literal', action='store_true', help='Usar solo literales (sin matches, 100% seguro)')
    parser.add_argument('--info',   action='store_true',     help='Solo mostrar info del slot, no modificar')
    args = parser.parse_args()

    bin_path = Path(args.out) if args.out else DATA_BIN_WORK

    if args.info:
        ensure_work_copy()
        row, foff, file_size, slot_size = get_file_info(args.id, DATA_BIN_ORIG)
        if foff is None:
            print(f"ID {args.id} no encontrado")
            return
        print(f"ID {args.id}:")
        print(f"  Offset en Data.bin: 0x{foff:08X}")
        print(f"  Tamaño FAT real:    {file_size:,} bytes")
        print(f"  Size se escribe en: fila {row['row'] + 1} @ 0x{size_field_write_offset(row):08X}")
        print(f"  Slot real:          {slot_size:,} bytes ({slot_size // 1024}KB)")
        # Leer header
        with open(DATA_BIN_ORIG, 'rb') as f:
            f.seek(foff)
            hdr = f.read(16)
        if hdr[:4] == b'LZ77':
            decomp_sz = struct.unpack_from('<I', hdr, 4)[0]
            comp_sz   = struct.unpack_from('<I', hdr, 8)[0]
            meta      = struct.unpack_from('<I', hdr, 12)[0]
            print(f"  LZ77 header (12+4):    decomp={decomp_sz:,} comp={comp_sz:,} meta=0x{meta:08X}")
        return

    ensure_work_copy()

    dec_path = Path(args.dec)
    if not dec_path.exists():
        print(f"ERROR: {dec_path} no existe")
        sys.exit(1)

    ok = patch_script(args.id, dec_path, bin_path, verify=args.verify, all_literal=args.all_literal)
    if ok:
        print(f"\n✓ Listo. Ahora reconstruye la ISO con:")
        print(f"    python traduccion_tools/build_iso.py")
    else:
        print(f"\n✗ Falló el patch.")
        sys.exit(1)


if __name__ == '__main__':
    main()

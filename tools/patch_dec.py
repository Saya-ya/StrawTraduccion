#!/usr/bin/env python3
"""
patch_dec.py — Script Rebuilder para Strawberry Panic! (PS2)

Flujo completo:
  1. Lee el .dec modificado (o aplica traducciones del CSV)
  2. Recomprime con lz77.compress() (ventana inicializada con 0x20)
  3. Calcula el tamaño REAL del slot (distancia al siguiente entry en FAT)
  4. Inyecta dentro del slot sin corromper archivos vecinos

IMPORTANTE: El campo 'size' de la FAT NO es el tamaño del archivo en disco.
El tamaño real del slot = offset_siguiente_entry - offset_este_entry.

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

DATA_BIN_ORIG = Path('originales/Data.bin')
DATA_BIN_WORK = Path('work/Data_patched.bin')
FAT_OFFSET    = 0x8004
FAT_ENTRIES   = 27411
ENTRY_SIZE    = 12  # bytes per FAT entry: [fid:u32, size_field:u32, foff:u32]


def load_fat(bin_path):
    """Lee la FAT completa y devuelve (entries_sorted, fat_raw)."""
    with open(bin_path, 'rb') as f:
        f.seek(FAT_OFFSET)
        fat_raw = f.read(FAT_ENTRIES * ENTRY_SIZE)

    entries = []
    for i in range(FAT_ENTRIES):
        fid, size_field, foff = struct.unpack_from('<III', fat_raw, i * ENTRY_SIZE)
        if foff > 0:
            entries.append({'fid': fid, 'size_field': size_field, 'foff': foff})

    entries_by_offset = sorted(entries, key=lambda e: e['foff'])
    return entries, entries_by_offset, fat_raw


def get_slot_info(target_fid, bin_path):
    """
    Devuelve (foff, real_slot_size) para un file ID.
    real_slot_size = distancia al siguiente entry en el archivo.
    """
    entries, entries_by_offset, _ = load_fat(bin_path)

    # Encontrar el entry
    target = None
    for e in entries:
        if e['fid'] == target_fid:
            target = e
            break
    if target is None:
        return None, None

    # Encontrar el siguiente entry por offset
    sorted_offsets = [e['foff'] for e in entries_by_offset]
    idx = sorted_offsets.index(target['foff'])
    if idx + 1 < len(sorted_offsets):
        next_foff = sorted_offsets[idx + 1]
        slot_size = next_foff - target['foff']
    else:
        slot_size = target['size_field']  # último entry, usar size_field

    return target['foff'], slot_size


def decompress_from_data_bin(target_fid, bin_path):
    """Descomprime el script de un file ID desde Data.bin."""
    foff, slot_size = get_slot_info(target_fid, bin_path)
    if foff is None:
        raise ValueError(f"ID {target_fid} no encontrado en FAT")

    with open(bin_path, 'rb') as f:
        f.seek(foff)
        raw = f.read(slot_size)

    if raw[:4] != b'LZ77':
        raise ValueError(f"ID {target_fid} en 0x{foff:08X} no es LZ77 (magic: {raw[:4].hex()})")

    return decompress(raw)


def inject_compressed(target_fid, comp_data, bin_path):
    """
    Inyecta datos comprimidos en el slot de un file ID.
    Solo escribe dentro del slot real (no corrompe vecinos).
    """
    foff, slot_size = get_slot_info(target_fid, bin_path)
    if foff is None:
        raise ValueError(f"ID {target_fid} no encontrado en FAT")

    if len(comp_data) > slot_size:
        raise ValueError(
            f"Datos ({len(comp_data):,} bytes) no caben en slot ({slot_size:,} bytes). "
            f"Necesita reubicación."
        )

    with open(bin_path, 'r+b') as f:
        f.seek(foff)
        f.write(comp_data)
        # Rellenar el resto del slot con ceros (dentro del slot solamente)
        f.write(b'\x00' * (slot_size - len(comp_data)))

    return foff, slot_size


def patch_script(target_fid, dec_path, bin_path, verify=False, verbose=True):
    """
    Pipeline completo: leer .dec → recomprimir → inyectar.
    """
    if verbose:
        print(f"[*] Procesando ID {target_fid} desde {dec_path}")

    foff, slot_size = get_slot_info(target_fid, bin_path)
    if foff is None:
        print(f"[!] ID {target_fid} no encontrado")
        return False

    if verbose:
        print(f"    Slot: 0x{foff:08X}, {slot_size:,} bytes ({slot_size // 1024}KB)")

    # Leer .dec modificado
    with open(dec_path, 'rb') as f:
        dec_data = f.read()

    # Leer metadata original (primeros 4 bytes del stream comprimido)
    with open(bin_path, 'rb') as f:
        f.seek(foff)
        orig_hdr = f.read(16)
    if orig_hdr[:4] == b'LZ77':
        orig_metadata = struct.unpack_from('<I', orig_hdr, 12)[0]
    else:
        orig_metadata = 0x000005EF

    if verbose:
        print(f"    Metadata original: 0x{orig_metadata:08X}")

    # Verificar round-trip antes de inyectar
    if verify:
        comp_test = compress(dec_data, metadata=orig_metadata)
        redec_test = decompress(comp_test)
        diffs = sum(a != b for a, b in zip(dec_data, redec_test))
        if diffs > 0 or len(dec_data) != len(redec_test):
            print(f"[!] FALLO round-trip: {diffs} diffs, "
                  f"orig={len(dec_data)}, redec={len(redec_test)}")
            return False
        if verbose:
            print(f"    Round-trip: OK (0 diffs)")

    # Recomprimir (metadata va al inicio del stream, header = 12 bytes)
    comp_data = compress(dec_data, metadata=orig_metadata)
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
    parser.add_argument('--info',   action='store_true',     help='Solo mostrar info del slot, no modificar')
    args = parser.parse_args()

    bin_path = Path(args.out) if args.out else DATA_BIN_WORK

    if args.info:
        ensure_work_copy()
        foff, slot_size = get_slot_info(args.id, DATA_BIN_ORIG)
        if foff is None:
            print(f"ID {args.id} no encontrado")
            return
        print(f"ID {args.id}:")
        print(f"  Offset en Data.bin: 0x{foff:08X}")
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

    ok = patch_script(args.id, dec_path, bin_path, verify=args.verify)
    if ok:
        print(f"\n✓ Listo. Ahora reconstruye la ISO con:")
        print(f"    python traduccion_tools/build_iso.py")
    else:
        print(f"\n✗ Falló el patch.")
        sys.exit(1)


if __name__ == '__main__':
    main()

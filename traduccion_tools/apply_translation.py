#!/usr/bin/env python3
"""
apply_translation.py — Aplica traducciones del CSV a Data.bin.

Lee el CSV con columnas [source, file_id, offset, original_text, translated_text].
Para cada fila con traducción:
  1. Convierte español → cirílico (mapeo de fuente)
  2. Codifica a UTF-16LE (scripts) o Shift-JIS (ELF)
  3. Si los bytes caben en el espacio original, parchea directo en stream comprimido
  4. Si no caben, reporta warning y salta

El mapeo español→cirílico vive AQUÍ en código, no en el CSV.

Uso:
    python apply_translation.py traduccion_tools/dialogue_scripts.csv
"""

import csv
import struct
import sys
from pathlib import Path

# Agregar tools/ al path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
from lz77 import decompress
from patch_compressed import trace_decompression
from datafat import read_entries, find_row
from glyph_map import SPANISH_TO_GLYPH, game_string, encode_game_utf16 as _enc_utf16, encode_game_sjis as _enc_sjis

# Re-exportar para compatibilidad
SPANISH_TO_GLYPH_UTF16 = SPANISH_TO_GLYPH


def encode_for_game_utf16(text):
    """Convierte texto español a UTF-16LE con mapeo cirílico."""
    return _enc_utf16(text)


def encode_for_game_sjis(text):
    """Convierte texto español a Shift-JIS con mapeo cirílico."""
    return _enc_sjis(text)


def patch_script_text(data_bin_path, file_id, dec_offset, new_bytes_utf16le):
    """
    Parchea texto en un script LZ77.
    Solo funciona si los bytes son LITERAL en el stream comprimido.
    """
    bin_path = Path(data_bin_path)
    
    rows = read_entries(bin_path)
    row = find_row(rows, file_id)
    if row is None:
        return False, f"ID {file_id} no encontrado"
    data_offset = row['off']
    orig_size = row['size']  # tamaño REAL: size_field de la fila siguiente
    
    with open(bin_path, 'rb') as f:
        f.seek(data_offset)
        raw = f.read(orig_size)
    
    if raw[:4] != b'LZ77':
        return False, "No es LZ77"
    
    expected_size = struct.unpack_from('<I', raw, 4)[0]
    comp_data = raw[12:]  # header es 12 bytes (magic + decomp_size + comp_size)
    out, mapping = trace_decompression(comp_data, expected_size)
    
    if dec_offset + len(new_bytes_utf16le) > len(out):
        return False, f"Fuera de rango"
    
    # Verificar que todos los bytes a modificar son LITERAL
    for i in range(dec_offset, dec_offset + len(new_bytes_utf16le)):
        if mapping[i][0] != 'LIT':
            return False, f"Byte {i} es MATCH, no se puede parchear"
    
    # Aplicar cambios
    comp_offsets = []
    with open(bin_path, 'r+b') as f:
        for i, new_byte in enumerate(new_bytes_utf16le):
            dec_i = dec_offset + i
            comp_pos = mapping[dec_i][1]
            comp_offsets.append(comp_pos)
            # comp_pos está mapeado contra raw[12:], así que el stream real
            # empieza en data_offset + 12 (header LZ77 de 12 bytes).
            abs_pos = data_offset + 12 + comp_pos
            f.seek(abs_pos)
            f.write(bytes([new_byte]))
    
    return True, f"OK ({len(new_bytes_utf16le)} bytes en {len(comp_offsets)} posiciones)"


def apply_translations(csv_path, data_bin_path):
    """Lee el CSV y aplica todas las traducciones."""
    bin_path = Path(data_bin_path)
    if not bin_path.exists():
        print(f"ERROR: {bin_path} no existe")
        return
    
    # Copias de trabajo
    work_path = bin_path.parent.parent / 'work' / 'Data_patched.bin'
    work_elf_path = bin_path.parent.parent / 'work' / 'SLPS_256.11_translated'
    
    import shutil
    work_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not work_path.exists() or work_path.stat().st_size != bin_path.stat().st_size:
        print(f"Creando copia de trabajo: {work_path}")
        shutil.copy2(bin_path, work_path)
        
    elf_orig = bin_path.parent / 'SLPS_256.11'
    if elf_orig.exists():
        if not work_elf_path.exists() or work_elf_path.stat().st_size != elf_orig.stat().st_size:
            print(f"Creando copia de trabajo ELF: {work_elf_path}")
            shutil.copy2(elf_orig, work_elf_path)
    
    stats = {'ok': 0, 'skip_size': 0, 'skip_match': 0, 'skip_other': 0}
    
    with open(csv_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        total = 0
        for row in reader:
            translated = row.get('translated_text', '').strip()
            if not translated:
                continue
            
            total += 1
            source = row.get('source', 'SCRIPT')
            file_id_str = row.get('file_id', '')
            offset_str = row.get('offset', '0x0')
            original = row.get('original_text', '')
            
            # Parse offset
            try:
                dec_offset = int(offset_str, 16)
            except:
                stats['skip_other'] += 1
                continue
            
            if source == 'ELF':
                # ELF usa Shift-JIS, tamaño fijo
                new_bytes = encode_for_game_sjis(translated)
                orig_bytes = original.encode('shift-jis')
                
                if len(new_bytes) <= len(orig_bytes):
                    # Parchear ELF (directo, sin compresión)
                    with open(work_elf_path, 'r+b') as elf:
                        elf.seek(dec_offset)
                        elf.write(new_bytes)
                        # Rellenar resto con espacios/nulos
                        padding = len(orig_bytes) - len(new_bytes)
                        if padding > 0:
                            elf.write(b'\x20' * padding)
                    stats['ok'] += 1
                else:
                    stats['skip_size'] += 1
                    if total <= 20:
                        print(f"  [!] '{translated[:40]}': {len(new_bytes)} > {len(orig_bytes)} bytes")
            
            elif source == 'SCRIPT':
                file_id = int(file_id_str)
                new_bytes = encode_for_game_utf16(translated)
                orig_bytes = original.encode('utf-16-le')
                
                if len(new_bytes) > len(orig_bytes):
                    stats['skip_size'] += 1
                    if total <= 20:
                        print(f"  [!] ID {file_id}: '{translated[:40]}' -> {len(new_bytes)} > {len(orig_bytes)} bytes")
                    continue
                
                # Pad with null bytes if shorter
                if len(new_bytes) < len(orig_bytes):
                    new_bytes = new_bytes + b'\x00' * (len(orig_bytes) - len(new_bytes))
                
                success, msg = patch_script_text(str(work_path), file_id, dec_offset, new_bytes)
                if success:
                    stats['ok'] += 1
                elif 'MATCH' in msg:
                    stats['skip_match'] += 1
                    if total <= 20:
                        print(f"  [!] ID {file_id}: '{translated[:40]}' -> MATCH (no parcheable)")
                else:
                    stats['skip_other'] += 1
                    if total <= 20:
                        print(f"  [!] ID {file_id}: {msg}")
            
            if total % 200 == 0:
                print(f"  Procesadas {total} traducciones...")
    
    print(f"\n=== Resultados ===")
    print(f"  Total procesadas:    {total}")
    print(f"  Aplicadas OK:        {stats['ok']}")
    print(f"  Saltadas (tamaño):   {stats['skip_size']}")
    print(f"  Saltadas (MATCH):    {stats['skip_match']}")
    print(f"  Saltadas (otro):     {stats['skip_other']}")
    print(f"\nData.bin modificado: {work_path}")
    return work_path


def main():
    if len(sys.argv) < 2:
        print("Uso: python apply_translation.py <dialogue.csv>")
        print("  python apply_translation.py traduccion_tools/dialogue_scripts.csv")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    data_bin = "originales/Data.bin"
    
    apply_translations(csv_path, data_bin)


if __name__ == '__main__':
    main()

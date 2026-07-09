"""
apply_translation.py — Applies translations from CSV to Data.bin.

Reads the CSV with columns [source, file_id, offset, original_text, translated_text].
For each row with a translation:
  1. Applies glyph mapping (e.g. Spanish → Cyrillic via font map)
  2. Encodes to UTF-16LE (scripts) or Shift-JIS (ELF)
  3. If bytes fit in the original space, patches directly in the compressed stream
  4. If not, reports a warning and skips

The glyph mapping lives HERE in code, not in the CSV.

Usage:
    python apply_translation.py traduccion_tools/dialogue_scripts.csv
"""

import csv
import struct
import sys
from pathlib import Path

# Add tools/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
from lz77 import decompress
from patch_compressed import trace_decompression
from datafat import read_entries, find_row
from glyph_map import SPANISH_TO_GLYPH, game_string, encode_game_utf16 as _enc_utf16, encode_game_sjis as _enc_sjis, get_glyph_map

# Re-export for compatibility
SPANISH_TO_GLYPH_UTF16 = SPANISH_TO_GLYPH


def encode_for_game_utf16(text, glyph_map=None):
    """Convierte texto a UTF-16LE con mapeo cirilico."""
    if glyph_map is None:
        return _enc_utf16(text)
    return _enc_utf16(text, glyph_map)


def encode_for_game_sjis(text, glyph_map=None):
    """Convierte texto a Shift-JIS con mapeo cirilico."""
    if glyph_map is None:
        return _enc_sjis(text)
    return _enc_sjis(text, glyph_map)


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
    orig_size = row['size']  # actual size: size_field of the next row
    
    with open(bin_path, 'rb') as f:
        f.seek(data_offset)
        raw = f.read(orig_size)
    
    if raw[:4] != b'LZ77':
        return False, "No es LZ77"
    
    expected_size = struct.unpack_from('<I', raw, 4)[0]
    comp_data = raw[12:]  # header is 12 bytes (magic + decomp_size + comp_size)
    out, mapping = trace_decompression(comp_data, expected_size)
    
    if dec_offset + len(new_bytes_utf16le) > len(out):
        return False, f"Fuera de rango"
    
    # Verify all bytes to modify are LITERAL
    for i in range(dec_offset, dec_offset + len(new_bytes_utf16le)):
        if mapping[i][0] != 'LIT':
            return False, f"Byte {i} es MATCH, no se puede parchear"
    
    # Apply changes
    comp_offsets = []
    with open(bin_path, 'r+b') as f:
        for i, new_byte in enumerate(new_bytes_utf16le):
            dec_i = dec_offset + i
            comp_pos = mapping[dec_i][1]
            comp_offsets.append(comp_pos)
            # comp_pos is mapped against raw[12:], so the real stream
            # starts at data_offset + 12 (LZ77 header is 12 bytes).
            abs_pos = data_offset + 12 + comp_pos
            f.seek(abs_pos)
            f.write(bytes([new_byte]))
    
    return True, f"OK ({len(new_bytes_utf16le)} bytes en {len(comp_offsets)} posiciones)"


def apply_translations(csv_path, data_bin_path, target_lang="es"):
    """Lee el CSV y aplica todas las traducciones."""
    glyph_map = get_glyph_map(target_lang) if target_lang != "es" else None
    bin_path = Path(data_bin_path)
    if not bin_path.exists():
        print(f"ERROR: {bin_path} no existe")
        return
    
    # Working copies
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
                # ELF uses Shift-JIS, fixed size
                new_bytes = encode_for_game_sjis(translated, glyph_map)
                orig_bytes = original.encode('shift-jis')
                
                if len(new_bytes) <= len(orig_bytes):
                    # Patch ELF (direct, no compression)
                    with open(work_elf_path, 'r+b') as elf:
                        elf.seek(dec_offset)
                        elf.write(new_bytes)
                        # Fill remainder with spaces/nulls
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
                new_bytes = encode_for_game_utf16(translated, glyph_map)
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
    import argparse
    parser = argparse.ArgumentParser(
        description='Aplica traducciones del CSV a Data.bin y ELF',
        usage='python apply_translation.py <dialogue.csv> [--target-lang LANG]'
    )
    parser.add_argument('csv_path', nargs='?', default=None, help='Ruta al CSV de traducciones')
    parser.add_argument('--target-lang', default='es',
                        choices=['es', 'en', 'custom'],
                        help='Idioma de traduccion (default: es)')
    args = parser.parse_args()

    if args.csv_path is None:
        parser.print_help()
        sys.exit(1)

    csv_path = args.csv_path
    data_bin = "originales/Data.bin"
    apply_translations(csv_path, data_bin, target_lang=args.target_lang)


if __name__ == '__main__':
    main()

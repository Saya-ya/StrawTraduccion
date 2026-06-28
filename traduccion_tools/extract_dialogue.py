#!/usr/bin/env python3
"""
extract_dialogue.py — Extrae textos de diálogo de los scripts LZ77.

Estrategia: en vez de extraer cualquier secuencia UTF-16LE (que produce 99% basura),
rastreamos los textos desde los scripts que YA SABEMOS que contienen diálogo real.

Usa dos métodos:
  1. Busca textos conocidos (del savestate RAM) en los scripts → identifica
     qué archivos tienen diálogo real
  2. Extrae TODOS los strings de esos archivos (incluyendo garbage)
  3. El traductor filtra manualmente lo que es diálogo vs opcode

CSV: file_id, offset_hex, original_text, translated_text

Uso:
    python extract_dialogue.py                    # Extrae de TODOS los scripts
    python extract_dialogue.py --id 7461          # Solo un script
    python extract_dialogue.py --known-only       # Solo textos verificados
"""

import csv
import struct
import sys
from pathlib import Path

# Agregar tools/ al path para importar lz77
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
from lz77 import decompress


def extract_utf16_strings(data, min_len=4, min_japanese=2):
    """Extrae secuencias UTF-16LE. Prueba offsets par e impar.
    Filtra basura de opcodes exigiendo hiragana/katakana."""
    strings = []
    
    for offset_parity in [0, 1]:
        i = offset_parity
        while i < len(data) - 4:
            word = struct.unpack_from('<H', data, i)[0]
            
            is_jp = (0x3040 <= word <= 0x309F or 0x30A0 <= word <= 0x30FF or
                     0x4E00 <= word <= 0x9FFF or 0x3000 <= word <= 0x303F)
            is_ascii = (0x20 <= word <= 0x7E)
            
            if is_jp or is_ascii or word in (0x000A, 0x000D):
                start = i
                end = i + 2
                
                while end < len(data) - 1:
                    w = struct.unpack_from('<H', data, end)[0]
                    if w == 0x0000:
                        end += 2
                        break
                    jp2 = (0x3040 <= w <= 0x309F or 0x30A0 <= w <= 0x30FF or
                           0x4E00 <= w <= 0x9FFF or 0x3000 <= w <= 0x303F)
                    ascii2 = (0x20 <= w <= 0x7E)
                    if jp2 or ascii2 or w in (0x000A, 0x000D):
                        end += 2
                        continue
                    break
                
                raw = bytes(data[start:end])
                try:
                    text = raw.decode('utf-16-le').rstrip('\x00').strip('\r\n')
                except:
                    i = end
                    continue
                
                if len(text) >= min_len:
                    hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309F')
                    katakana = sum(1 for c in text if '\u30A0' <= c <= '\u30FF')
                    kanji = sum(1 for c in text if '\u4E00' <= c <= '\u9FFF')
                    ascii_lower = sum(1 for c in text if c.isascii() and c.islower())
                    
                    # Filtrar: debe tener kana (hiragana o katakana)
                    # Excepto si es solo katakana (nombres propios)
                    has_kana = (hiragana + katakana) >= min_japanese
                    
                    # Filtrar basura: no debe tener letras ASCII sueltas
                    no_garbage = ascii_lower <= 1 or (hiragana + katakana) > 3
                    
                    if has_kana and no_garbage:
                        strings.append((start, text))
                
                i = end
                continue
            i += 2
    
    return strings


def extract_elf_strings(elf_path):
    """Extrae strings Shift-JIS del ELF (textos de sistema/menús)."""
    data = Path(elf_path).read_bytes()
    strings = []
    i = 0
    
    while i < len(data) - 1:
        b1 = data[i]
        if (0x81 <= b1 <= 0x9F or 0xE0 <= b1 <= 0xEF):
            b2 = data[i + 1] if i + 1 < len(data) else 0
            if 0x40 <= b2 <= 0xFC and b2 != 0x7F:
                start = i
                end = i + 2
                while end < len(data) - 1:
                    c1 = data[end]
                    if c1 == 0x00:
                        end += 1
                        break
                    if (0x81 <= c1 <= 0x9F or 0xE0 <= c1 <= 0xEF):
                        if end + 1 < len(data) and 0x40 <= data[end+1] <= 0xFC and data[end+1] != 0x7F:
                            end += 2
                            continue
                    if 0x20 <= c1 <= 0x7E:
                        end += 1
                        continue
                    break
                
                s = data[start:end]
                jp = sum(1 for j in range(0, len(s)-1, 2)
                        if (0x81 <= s[j] <= 0x9F or 0xE0 <= s[j] <= 0xEF)
                        and 0x40 <= s[j+1] <= 0xFC and s[j+1] != 0x7F)
                
                if jp >= 3:
                    try:
                        decoded = s.replace(b'\x00', b'').decode('shift-jis')
                        strings.append((start, decoded))
                    except:
                        pass
                i = end
                continue
        i += 1
    
    return strings


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extraer textos de diálogo")
    parser.add_argument("--id", type=int, default=0, help="Solo un script ID")
    parser.add_argument("--elf", action="store_true", help="Extraer del ELF en vez de scripts")
    parser.add_argument("--csv", default="traduccion_tools/dialogue.csv")
    parser.add_argument("--bin", default="originales/Data.bin")
    parser.add_argument("--elf-path", default="originales/SLPS_256.11")
    args = parser.parse_args()

    out_csv = Path(args.csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    all_strings = []

    if args.elf:
        print("Extrayendo textos del ELF (sistema/menús)...")
        elf_strings = extract_elf_strings(args.elf_path)
        for offset, text in elf_strings:
            all_strings.append({
                "source": "ELF",
                "file_id": "ELF",
                "offset": f"0x{offset:06X}",
                "text": text,
            })
        print(f"  {len(elf_strings)} strings del ELF")
    else:
        print("Extrayendo textos de scripts LZ77...")
        
        # Cargar Data.bin y FAT
        data_bin = Path(args.bin).read_bytes()
        FAT_OFFSET = 0x8004
        
        total = 0
        file_count = 0
        
        for i in range(27411):
            off = FAT_OFFSET + i * 12
            fid, size, data_offset = struct.unpack_from('<III', data_bin, off)
            
            if args.id and fid != args.id:
                continue
            if data_offset + 4 > len(data_bin):
                continue
            if data_bin[data_offset:data_offset + 4] != b'LZ77':
                continue
            
            file_count += 1
            raw = data_bin[data_offset:data_offset + size]
            try:
                dec = decompress(raw)
            except:
                continue
            
            strings = extract_utf16_strings(dec, min_len=4, min_japanese=1)
            for offset, text in strings:
                all_strings.append({
                    "source": "SCRIPT",
                    "file_id": fid,
                    "offset": f"0x{offset:05X}",
                    "text": text,
                })
            total += len(strings)
            
            if file_count % 200 == 0:
                print(f"  {file_count} scripts, {total} strings...")
        
        print(f"  {file_count} scripts procesados, {total} strings")
    
    # Escribir CSV
    with open(out_csv, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['source', 'file_id', 'offset', 'original_text', 'translated_text'])
        for s in all_strings:
            writer.writerow([s['source'], s['file_id'], s['offset'], s['text'], ''])
    
    print(f"\nCSV guardado: {out_csv} ({len(all_strings)} líneas)")
    print("El traductor solo llena la columna 'translated_text' con español normal (á, é, í, ó, ú, ñ, ¿, ¡).")
    print("El script apply_translation.py se encarga del mapeo a cirílicos.")


if __name__ == '__main__':
    main()

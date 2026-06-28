#!/usr/bin/env python3
"""
extract_dialogue.py — Extrae textos de diálogo de los scripts LZ77.

Estrategia mejorada: 
Dado que el bytecode produce miles de falsos positivos en UTF-16LE, 
esta versión aplica una heurística lingüística estricta:
1. El texto debe tener una proporción mínima de Hiragana/Katakana.
2. Debe contener partículas japonesas comunes (の, は, が, に, を, て, で, です, ます).
3. No debe tener caracteres de control ni "basura" común de desalineación.
"""

import csv
import struct
import sys
import re
from pathlib import Path

# Agregar tools/ al path para importar lz77
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
from lz77 import decompress

def is_valid_japanese_text(text):
    if len(text) < 4:
        return False
        
    # Caracteres prohibidos que suelen aparecer como basura UTF-16
    forbidden = ['ヿ', '眀', '矻', '矺', '惹', 'ヺ', '昀', 'ヴ', '簈', 'ヒ', '揣']
    for f in forbidden:
        if f in text:
            return False

    # Contar tipos de caracteres
    hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309F')
    katakana = sum(1 for c in text if '\u30A0' <= c <= '\u30FF')
    kanji = sum(1 for c in text if '\u4E00' <= c <= '\u9FFF')
    ascii_chars = sum(1 for c in text if c.isascii())
    
    total_valid = hiragana + katakana + kanji
    if total_valid == 0:
        return False
        
    # El texto debe ser al menos 50% caracteres japoneses válidos
    if total_valid / len(text) < 0.5:
        return False
        
    # Las oraciones reales casi siempre usan hiragana para la gramática
    if hiragana == 0 and kanji > 0:
        return False
        
    # Buscar partículas/gramática común (esencial para filtrar basura)
    common_particles = ['の', 'は', 'が', 'に', 'を', 'て', 'で', 'と', 'から', 'まで', 'か', 'な', 'だ', 'し', 'い', 'う', 'る', '？', '！', '、', '。', '「', '」', '…']
    has_grammar = any(p in text for p in common_particles)
    
    # Excepción para nombres propios cortos que podrían ser solo kanji/katakana
    if len(text) <= 8 and (katakana + kanji) == len(text):
        return True
        
    return has_grammar

def extract_utf16_strings(data, min_len=4):
    strings = []
    
    for offset_parity in [0, 1]:
        i = offset_parity
        while i < len(data) - 4:
            word = struct.unpack_from('<H', data, i)[0]
            
            is_jp = (0x3040 <= word <= 0x309F or 0x30A0 <= word <= 0x30FF or
                     0x4E00 <= word <= 0x9FFF or 0x3000 <= word <= 0x303F)
            
            if is_jp:
                start = i
                end = i + 2
                
                while end < len(data) - 1:
                    w = struct.unpack_from('<H', data, end)[0]
                    if w == 0x0000:
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
                
                if is_valid_japanese_text(text):
                    strings.append((start, text))
                
                i = end
                continue
            i += 2
    
    return strings

def extract_elf_strings(elf_path):
    data = Path(elf_path).read_bytes()
    strings = []
    i = 0
    while i < len(data) - 1:
        b1 = data[i]
        if (0x81 <= b1 <= 0x9F) or (0xE0 <= b1 <= 0xEF):
            start = i
            while i < len(data) - 1:
                b = data[i]
                if (0x81 <= b <= 0x9F) or (0xE0 <= b <= 0xEF):
                    i += 2
                elif 0x20 <= b <= 0x7E:
                    i += 1
                else:
                    break
            
            raw = data[start:i]
            if len(raw) >= 4:
                try:
                    text = raw.decode('shift-jis').strip()
                    if text and not all(c.isascii() for c in text):
                        strings.append((start, text))
                except:
                    pass
            continue
        i += 1
    return strings

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, help="Solo un script ID")
    parser.add_argument("--elf", action="store_true", help="Extraer del ELF en vez de scripts")
    parser.add_argument("--csv", default="textos/dialogo.csv")
    parser.add_argument("--bin", default="originales/Data.bin")
    parser.add_argument("--elf-path", default="originales/SLPS_256.11")
    args = parser.parse_args()
    
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    if args.elf:
        print(f"Extrayendo textos del ELF ({args.elf_path})...")
        strings = extract_elf_strings(args.elf_path)
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['source', 'file_id', 'offset', 'original_text', 'translated_text'])
            for offset, text in strings:
                writer.writerow(['ELF', 'ELF', f"0x{offset:06X}", text, ''])
        print(f"Guardado en {csv_path} ({len(strings)} strings)")
        return
        
    print("Extrayendo textos de scripts LZ77...")
    bin_path = Path(args.bin)
    data_bin = bin_path.read_bytes()
    
    # Parse FAT
    FAT_OFFSET = 0x8004
    NUM_ENTRIES = 27411
    entries = []
    for i in range(NUM_ENTRIES):
        off = FAT_OFFSET + i * 12
        fid, size, data_offset = struct.unpack("<III", data_bin[off:off+12])
        if size > 0:
            entries.append({"id": fid, "size": size, "offset": data_offset})
            
    if args.id:
        entries = [e for e in entries if e["id"] == args.id]
        
    all_strings = []
    count = 0
    
    for entry in entries:
        off = entry["offset"]
        size = entry["size"]
        raw = data_bin[off:off+size]
        
        if raw[:4] == b"LZ77":
            try:
                dec = decompress(raw)
                strings = extract_utf16_strings(dec)
                for offset, text in strings:
                    all_strings.append((entry["id"], offset, text))
                count += 1
                if count % 200 == 0:
                    print(f"  {count} scripts procesados, {len(all_strings)} textos encontrados...")
            except:
                pass
                
    print(f"  {count} scripts LZ77 analizados, {len(all_strings)} textos reales encontrados")
    
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['source', 'file_id', 'offset', 'original_text', 'translated_text'])
        for fid, offset, text in all_strings:
            writer.writerow(['SCRIPT', fid, f"0x{offset:05X}", text, ''])
            
    print(f"CSV guardado: {csv_path}")

if __name__ == "__main__":
    main()

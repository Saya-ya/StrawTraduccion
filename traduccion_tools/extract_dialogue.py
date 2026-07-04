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
from datafat import read_entries

def _is_jp_codepoint(cp: int) -> bool:
    """Un code point Unicode que puede aparecer en texto japonés."""
    return (
        0x3040 <= cp <= 0x309F   # Hiragana
        or 0x30A0 <= cp <= 0x30FF  # Katakana
        or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs (Kanji)
        or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
        or 0xFF00 <= cp <= 0xFFEF  # Fullwidth Forms (digits, letters, punctuation)
        or 0xFF5F <= cp <= 0xFF9F  # Halfwidth Katakana
        or 0x2000 <= cp <= 0x206F  # General Punctuation (dashes, ellipsis … etc.)
    )


def _is_jp_or_punct(cp: int) -> bool:
    """Character that can appear adjacent to Japanese text (prefix/suffix)."""
    return _is_jp_codepoint(cp)


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

            if _is_jp_codepoint(word):
                start = i
                end = i + 2

                while end < len(data) - 1:
                    w = struct.unpack_from('<H', data, end)[0]
                    if w == 0x0000:
                        break
                    ascii2 = (0x20 <= w <= 0x7E)
                    if _is_jp_codepoint(w) or ascii2 or w in (0x000A, 0x000D):
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
                    # Walk back to include leading fullwidth/CJK characters
                    # (e.g. ３ before 校 in "３校の中で…") that the scanner missed
                    # because they are not in the traditional JP detection ranges.
                    orig_start = start
                    while start >= 2 and data[start - 2:start] != b'\x00\x00':
                        prev_cp = struct.unpack_from('<H', data, start - 2)[0]
                        if _is_jp_or_punct(prev_cp) or prev_cp == 0x0000:
                            start -= 2
                            continue
                        break
                    if start != orig_start:
                        text = data[start:end].decode('utf-16-le').rstrip('\x00').strip('\r\n')
                    strings.append((start, text))

                i = end
                continue
            i += 2

    return strings

import re

# Rangos Unicode para caracteres japoneses
_JP_RE = re.compile(
    r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2000-\u206f]'
)
_HIRAGANA_RE = re.compile(r'[\u3040-\u309f]')
_KATAKANA_RE = re.compile(r'[\u30a0-\u30ff\uff65-\uff9f]')


def is_valid_elf_text(text: str) -> bool:
    """Filtro estricto para textos extraidos del ELF (Shift-JIS).

    Requiere:
      - >=4 caracteres japoneses
      - >50% del texto son caracteres japoneses
      - >=4 caracteres japoneses consecutivos (evita kanji aislado entre ASCII)
      - Contiene hiragana (gramatica) O es todo katakana (menus/UI)
    """
    jp_chars = _JP_RE.findall(text)
    if len(jp_chars) < 4:
        return False
    if len(jp_chars) / len(text) < 0.5:
        return False

    # Maximo de caracteres JP consecutivos
    max_cons = 0
    cur = 0
    for ch in text:
        if _JP_RE.match(ch):
            cur += 1
            max_cons = max(max_cons, cur)
        else:
            cur = 0
    if max_cons < 4:
        return False

    # Tiene hiragana? => es texto real con gramatica
    if _HIRAGANA_RE.search(text):
        return True

    # Si no tiene hiragana, verificar que todo lo no-ASCII sea katakana (menus)
    non_ascii = [
        ch for ch in text
        if ord(ch) > 127 and ch not in '\u3000\u3001\u3002\uff01\uff1f\u2026\u300c\u300d\uff08\uff09'
    ]
    if non_ascii and all(_KATAKANA_RE.match(ch) for ch in non_ascii):
        return True

    return False


def extract_elf_strings(elf_path, filter_garbage=True):
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
                        if not filter_garbage or is_valid_elf_text(text):
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
        file_exists = csv_path.exists()
        mode = 'a' if file_exists else 'w'
        with open(csv_path, mode, encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['source', 'file_id', 'offset', 'section', 'section_order',
                                 'original_text', 'translated_text'])
            for offset, text in strings:
                writer.writerow(['ELF', 'ELF', f"0x{offset:06X}", '0', '0', text, ''])
        print(f"Guardado en {csv_path} ({len(strings)} strings)")
        return
        
    print("Extrayendo textos de scripts LZ77...")
    bin_path = Path(args.bin)
    data_bin = bin_path.read_bytes()
    
    # Parse FAT con el formato real:
    # el tamaño de un ID vive en el size_field de la fila siguiente.
    entries = [
        {"id": r["id"], "size": r["size"], "offset": r["off"]}
        for r in read_entries(data_bin) if r["is_file"]
    ]
            
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

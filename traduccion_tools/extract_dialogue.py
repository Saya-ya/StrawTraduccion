import csv
import re
from pathlib import Path


_JP_RE = re.compile(
    r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2000-\u206f]'
)
_HIRAGANA_RE = re.compile(r'[\u3040-\u309f]')
_KATAKANA_RE = re.compile(r'[\u30a0-\u30ff\uff65-\uff9f]')


def is_valid_elf_text(text: str) -> bool:
    jp_chars = _JP_RE.findall(text)
    if len(jp_chars) < 4:
        return False
    if len(jp_chars) / len(text) < 0.5:
        return False

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

    if _HIRAGANA_RE.search(text):
        return True

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
    parser = argparse.ArgumentParser(
        description="Extrae textos del ELF (SLPS_256.11) usando Shift-JIS."
    )
    parser.add_argument("--elf", action="store_true", required=True,
                        help="Extraer del ELF (requerido)")
    parser.add_argument("--csv", default="textos/dialogo.csv")
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


if __name__ == "__main__":
    main()

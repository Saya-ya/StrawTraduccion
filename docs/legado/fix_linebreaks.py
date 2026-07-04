#!/usr/bin/env python3
"""
Corrige saltos de linea en traducciones que no los heredaron del original.
Inserta \r\n en la traduccion en posiciones proporcionales a las del japones,
respetando limites de palabra.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
from tools.glyph_map import encode_game_utf16

DB_PATH = PROJECT_ROOT / "work" / "translation_manager.db"


def find_break_proportions(original_text):
    """Extrae las posiciones proporcionales (0.0-1.0) de cada \r\n."""
    proportions = []
    clean = original_text.replace('\r\n', '')
    if not clean:
        return []

    # Walk through original finding \r\n positions
    pos_in_clean = 0
    i = 0
    while i < len(original_text):
        if original_text[i:i+2] == '\r\n':
            if clean and pos_in_clean < len(clean):
                proportions.append(pos_in_clean / len(clean))
            i += 2
        else:
            pos_in_clean += 1
            i += 1
    return proportions


def insert_breaks_at_proportions(text, proportions):
    """Inserta \r\n en el texto en posiciones proporcionales,
    buscando espacios o puntuacion cercanos. Si no hay buen punto, omite el break."""
    if not proportions or len(text) < 4:
        return text

    # Caracteres validos para saltar DESPUES de ellos
    BREAK_CHARS = ' .,;:!?¡¿…)@%—–-'
    SEARCH_WINDOW = 12  # chars hacia adelante y atras

    result = list(text)
    inserted = 0

    for prop in proportions:
        target_pos = int(prop * len(text))

        # Buscar el mejor punto de corte en una ventana alrededor
        best = None
        best_dist = SEARCH_WINDOW + 1

        for direction, start, end in [(-1, target_pos, max(0, target_pos - SEARCH_WINDOW)),
                                        (1, target_pos, min(len(text), target_pos + SEARCH_WINDOW))]:
            for candidate in range(start, end, direction):
                if candidate < 0 or candidate >= len(text):
                    continue
                ch = text[candidate]
                if ch in BREAK_CHARS:
                    dist = abs(candidate - target_pos)
                    if dist < best_dist:
                        best_dist = dist
                        best = candidate + 1  # insertar DESPUES del separador
                    break  # tomar el primero en esa direccion
                # Tambien considerar cambios de mayuscula (inicio de frase)
                if direction == 1 and ch.isupper() and candidate > target_pos:
                    dist = abs(candidate - target_pos)
                    if dist < best_dist and candidate > 0 and text[candidate - 1] in ' .':
                        if dist < best_dist:
                            best_dist = dist
                            best = candidate

        # Si no encontramos buen punto, omitir este break
        if best is None or best_dist > SEARCH_WINDOW:
            continue

        # Posicion real considerando inserciones previas
        actual_pos = best + inserted

        # No insertar en extremos ni duplicar
        if actual_pos <= 0 or actual_pos >= len(result):
            continue

        # Evitar duplicados: si ya hay \r\n en las cercanias, saltar
        zone = ''.join(result[max(0, actual_pos - 4):min(len(result), actual_pos + 4)])
        if '\r\n' in zone:
            continue

        result[actual_pos:actual_pos] = '\r\n'
        inserted += 2

    # Limpiar: eliminar breaks que quedaron al final
    final = ''.join(result)
    while final.endswith('\r\n'):
        final = final[:-2]
    # Eliminar breaks multiples consecutivos (>2)
    import re
    final = re.sub(r'(\r\n){3,}', '\r\n\r\n', final)

    return final


def needs_fix(original, translated):
    """Determina si la traduccion necesita que se le inserten saltos."""
    if not translated or not original:
        return False
    return '\r\n' in original and '\r\n' not in translated


def fix_script(conn, script_id, dry_run=False):
    """Corrige los saltos de linea de todas las entradas de un script."""
    rows = conn.execute(
        """SELECT id, original_text, translated_text, segment_capacity, fit_status
           FROM text_entries
           WHERE script_id = ? AND is_translated = 1
           AND original_text LIKE '%' || char(13) || char(10) || '%'""",
        (script_id,),
    ).fetchall()

    fixed = 0
    skipped = 0
    overflow = 0

    for row in rows:
        eid, original, translated, capacity, fit_status = row

        if not needs_fix(original, translated):
            skipped += 1
            continue

        proportions = find_break_proportions(original)
        if not proportions:
            skipped += 1
            continue

        fixed_text = insert_breaks_at_proportions(translated, proportions)

        if fixed_text == translated:
            skipped += 1
            continue

        # Validar fit
        encoded = encode_game_utf16(fixed_text)
        new_size = len(encoded) + 2

        if new_size > capacity:
            overflow += 1
            continue

        new_status = 'tight' if capacity - new_size < 20 else 'ok'

        if not dry_run:
            conn.execute(
                """UPDATE text_entries
                   SET translated_text = ?,
                       fit_status = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (fixed_text, new_status, eid),
            )
        fixed += 1

    return fixed, skipped, overflow


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Inserta saltos de linea en traducciones')
    parser.add_argument('scripts', nargs='*', help='IDs de scripts a corregir (si no, todos)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--all', action='store_true', help='Todos los scripts traducidos')
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if args.all or not args.scripts:
        # Todos los scripts con traducciones de DeepSeek (8010+)
        scripts = conn.execute(
            """SELECT id FROM scripts
               WHERE CAST(id AS INTEGER) >= 8010
               AND translated_texts > 0
               ORDER BY CAST(id AS INTEGER)"""
        ).fetchall()
        script_ids = [r['id'] for r in scripts]
    else:
        script_ids = args.scripts

    print(f"Scripts a procesar: {len(script_ids)}")
    print(f"Modo: {'dry-run' if args.dry_run else 'ESCRITURA'}")
    print()

    total_fixed = 0
    total_overflow = 0

    for sid in script_ids:
        fixed, skipped, ovf = fix_script(conn, sid, dry_run=args.dry_run)
        if fixed > 0 or ovf > 0:
            print(f"  {sid}: {fixed} corregidas, {ovf} overflow, {skipped} ok")
        total_fixed += fixed
        total_overflow += ovf

    if not args.dry_run:
        conn.commit()

    conn.close()

    print()
    print(f"Total corregidas: {total_fixed}")
    print(f"Total overflow: {total_overflow}")
    if args.dry_run:
        print("(dry-run: sin cambios)")


if __name__ == '__main__':
    main()

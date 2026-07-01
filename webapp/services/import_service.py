"""
Import service — wraps extract_dialogue.py as subprocess,
parses CSV, populates DB with smart matching (agrupado por script_id).
"""
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / 'tools'))
sys.path.insert(0, str(PROJECT_ROOT / 'traduccion_tools'))

from sqlalchemy.orm import Session
from ..config import TIMEOUT_EXTRACT, WORK, TEXTOS, ORIGINALES
from ..database import Script, TextEntry, get_session
from .capacity import compute_capacity
from .fit_checker import check_fit


def run_extraction(output_csv: Path) -> dict:
    """Ejecuta extract_dialogue.py como subprocess. Devuelve stats."""
    extract_scripts = PROJECT_ROOT / 'traduccion_tools' / 'extract_dialogue.py'

    stats = {"scripts": 0, "elf": 0, "errors": []}

    # Extraer scripts
    r1 = subprocess.run(
        ["python3", str(extract_scripts), "--csv", str(output_csv),
         "--bin", str(ORIGINALES / "Data.bin")],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_EXTRACT
    )
    if r1.returncode != 0:
        stats["errors"].append(f"Script extraction failed: {r1.stderr[-300:]}")

    # Extraer ELF
    r2 = subprocess.run(
        ["python3", str(extract_scripts), "--elf", "--csv", str(output_csv),
         "--elf-path", str(ORIGINALES / "SLPS_256.11")],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_EXTRACT
    )
    if r2.returncode != 0:
        stats["errors"].append(f"ELF extraction failed: {r2.stderr[-300:]}")

    return stats


def parse_csv(csv_path: Path) -> list[dict]:
    """Lee el CSV y devuelve lista de dicts con los campos normalizados."""
    rows = []
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = row.get('source', 'SCRIPT').strip()
            file_id = row.get('file_id', '0').strip()
            offset_str = row.get('offset', '0x0').strip()
            original = row.get('original_text', '')
            translated = row.get('translated_text', '').strip()

            try:
                byte_offset = int(offset_str, 16)
            except ValueError:
                continue

            # ELF usa script_id especial -1
            if source == 'ELF' or file_id == 'ELF':
                script_id = -1
            else:
                try:
                    script_id = int(file_id)
                except ValueError:
                    script_id = -1

            rows.append({
                'source': source,
                'script_id': script_id,
                'byte_offset': byte_offset,
                'original_text': original,
                'translated_text': translated,
            })
    return rows


def _group_translations_by_script(
    existing_entries: list[TextEntry]
) -> dict:
    """Pre-agrupa traducciones existentes por script_id para busqueda O(1).

    Devuelve dict: (script_id, byte_offset, original_text) -> translated_text
    para paso 1 (match exacto), y dict: script_id -> [(offset, original, translated)]
    para pasos 2 y 3 (fallback).
    """
    exact = {}
    by_script = {}

    for entry in existing_entries:
        if not entry.translated_text:
            continue
        sid = entry.script_id
        key = (sid, entry.byte_offset, entry.original_text)
        exact[key] = entry.translated_text

        if sid not in by_script:
            by_script[sid] = []
        by_script[sid].append(
            (entry.byte_offset, entry.original_text, entry.translated_text)
        )

    return exact, by_script


def _similarity(a: str, b: str) -> float:
    """Ratio de similitud simple (caracteres en comun / max len)."""
    if not a or not b:
        return 0.0
    common = sum(1 for ca, cb in zip(a, b) if ca == cb)
    return common / max(len(a), len(b))


def match_translation(
    new_entry: dict,
    exact_map: dict,
    by_script: dict,
    stats: dict
) -> Optional[str]:
    """Empareja una entrada recien extraida con traducciones existentes.

    Paso 1: match exacto (script_id + offset + original_text)
    Paso 2: fallback por contenido dentro del mismo script_id (ignora offset)
    Paso 3: fallback fuzzy (>95% similitud) dentro de ±16 bytes en el mismo script
    """
    sid = new_entry['script_id']
    off = new_entry['byte_offset']
    orig = new_entry['original_text']

    # Paso 1: exacto
    key = (sid, off, orig)
    if key in exact_map:
        stats['matched_exact'] += 1
        return exact_map[key]

    # Paso 2: mismo script_id + mismo texto, ignorando offset
    script_entries = by_script.get(sid, [])
    for eoff, eorig, etrans in script_entries:
        if eorig == orig:
            stats['matched_content'] += 1
            return etrans

    # Paso 3: fuzzy (±16 bytes, >95% similitud, mismo script_id)
    for eoff, eorig, etrans in script_entries:
        if abs(eoff - off) <= 16 and _similarity(orig, eorig) > 0.95:
            stats['matched_fuzzy'] += 1
            return etrans

    stats['unmatched'] += 1
    return None


def _compute_script_capacities(script_id: int, entries: list[dict]) -> dict:
    """Calcula segment capacity para entries de un script."""
    dec_path = PROJECT_ROOT / 'work' / 'scripts_extraidos' / f'ID_{script_id:05d}.dec'
    if not dec_path.exists():
        return {}
    dec_data = dec_path.read_bytes()
    result = {}
    for entry in entries:
        off = entry['byte_offset']
        orig = entry['original_text']
        try:
            cap = compute_capacity(dec_data, off, orig)
            result[off] = cap
        except Exception:
            result[off] = 0
    return result


def _load_csv_translations(csv_path: Path) -> dict:
    """Carga traducciones desde un CSV existente.
    Retorna dict: (script_id, byte_offset, original_text) -> translated_text
    """
    result = {}
    rows = parse_csv(csv_path)
    for row in rows:
        if row['translated_text']:
            key = (row['script_id'], row['byte_offset'], row['original_text'])
            result[key] = row['translated_text']
    return result


def import_csv_to_db(csv_path: Path = None) -> dict:
    """
    Pipeline completo de importacion:
    1. Ejecuta extract_dialogue.py → CSV temporal (NO sobreescribe el existente)
    2. Parsea el CSV temporal
    3. Si existe un CSV previo con traducciones, las carga como base de matching
    4. Hace matching contra DB existente (agrupado por script_id)
    5. Upsert en DB
    """
    if csv_path is None:
        csv_path = TEXTOS / 'dialogo.csv'

    session = get_session()
    stats = {
        'matched_exact': 0,
        'matched_content': 0,
        'matched_fuzzy': 0,
        'unmatched': 0,
        'new': 0,
        'updated': 0,
        'total': 0,
        'errors': [],
    }

    # 1. Extraer a CSV temporal (no sobreescribe el existente)
    temp_csv = WORK / 'build_temp' / '_extract_temp.csv'
    temp_csv.parent.mkdir(parents=True, exist_ok=True)
    ext_stats = run_extraction(temp_csv)
    stats['errors'].extend(ext_stats.get('errors', []))

    # 2. Parsear el CSV temporal (nuevas entradas)
    new_rows = parse_csv(temp_csv)
    stats['total'] = len(new_rows)

    # 3. Cargar traducciones del CSV existente (si hay)
    existing_csv_translations = {}
    if csv_path.exists():
        existing_csv_translations = _load_csv_translations(csv_path)

    # 4. Cargar traducciones de la DB existente
    existing_db = session.query(TextEntry).filter(
        TextEntry.translated_text != ''
    ).all()
    exact_map, by_script = _group_translations_by_script(existing_db)

    # Merge CSV translations into exact_map (DB tiene prioridad si hay conflicto)
    for key, trans in existing_csv_translations.items():
        if key not in exact_map:
            exact_map[key] = trans
            # Agregar al by_script para fallback
            sid = key[0]
            if sid not in by_script:
                by_script[sid] = []
            by_script[sid].append((key[1], key[2], trans))

    # 4. Cargar entries existentes en DB para upsert (todas, no solo traducidas)
    existing_map = {}
    for e in session.query(TextEntry).all():
        existing_map[(e.script_id, e.byte_offset, e.original_text)] = e

    # Agrupar nuevas por script_id para crear/actualizar Script
    script_ids_in_csv = set()
    new_entries_by_script = {}
    for row in new_rows:
        sid = row['script_id']
        script_ids_in_csv.add(sid)
        if sid not in new_entries_by_script:
            new_entries_by_script[sid] = []
        new_entries_by_script[sid].append(row)

    # 5. Asegurar scripts en DB
    existing_scripts = {
        s.id: s for s in session.query(Script).all()
    }

    for sid in script_ids_in_csv:
        if sid not in existing_scripts:
            script = Script(id=sid, source='SCRIPT' if sid != -1 else 'ELF')
            session.add(script)
            existing_scripts[sid] = script

    # 6. Upsert text_entries
    for sid, entries in new_entries_by_script.items():
        # Pre-computar capacidades para SCRIPT entries
        capacities = {}
        if sid != -1:  # -1 = ELF, no tiene .dec
            try:
                # Convert entries to expected format
                cap_entries = [{'byte_offset': r['byte_offset'],
                               'original_text': r['original_text']} for r in entries]
                capacities = _compute_script_capacities(sid, cap_entries)
            except Exception:
                pass

        for row in entries:
            key = (sid, row['byte_offset'], row['original_text'])
            translated = match_translation(row, exact_map, by_script, stats)
            off = row['byte_offset']
            cap = capacities.get(off, 0)

            if key in existing_map:
                entry = existing_map[key]
                if translated and translated != entry.translated_text:
                    entry.translated_text = translated
                    entry.is_translated = True
                if cap and not entry.segment_capacity:
                    entry.segment_capacity = cap
                if entry.translated_text:
                    fit = check_fit(entry.translated_text, entry.source, cap or entry.original_bytes or 999)
                    entry.fit_status = fit['status']
                    entry.needs_shift = (fit['status'] == 'needs_shift')
                    entry.original_bytes = len(row['original_text'].encode('utf-16-le') if entry.source == 'SCRIPT' else row['original_text'].encode('shift-jis'))
                stats['updated'] += 1
            else:
                orig_bytes = len(row['original_text'].encode('utf-16-le') if row['source'] == 'SCRIPT' else row['original_text'].encode('shift-jis'))
                entry = TextEntry(
                    script_id=sid,
                    source=row['source'],
                    byte_offset=off,
                    original_text=row['original_text'],
                    translated_text=translated or '',
                    original_bytes=orig_bytes,
                    segment_capacity=cap,
                    is_translated=bool(translated),
                    fit_status='unchecked',
                )
                if translated:
                    fit = check_fit(translated, entry.source, cap or orig_bytes or 999)
                    entry.fit_status = fit['status']
                    entry.needs_shift = (fit['status'] == 'needs_shift')
                session.add(entry)
                stats['new'] += 1

    # 7. Actualizar contadores en scripts
    for sid, script in existing_scripts.items():
        if sid in script_ids_in_csv:
            script.total_texts = len(new_entries_by_script[sid])
            script.translated_texts = sum(
                1 for row in new_entries_by_script[sid]
                if row.get('translated_text') or match_translation(row, exact_map, by_script, {'matched_exact':0,'matched_content':0,'matched_fuzzy':0,'unmatched':0})
            )

    session.commit()
    session.close()

    return stats

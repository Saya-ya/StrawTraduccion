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


def _load_csv_translations(csv_path: Path) -> dict:
    translations = {}
    if not csv_path.exists():
        return translations
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            translated = (row.get('translated_text') or '').strip()
            if not translated:
                continue
            source = (row.get('source') or 'SCRIPT').strip()
            file_id = (row.get('file_id') or '0').strip()
            offset = (row.get('offset') or '0x0').strip()
            original = row.get('original_text') or ''
            try:
                sid = int(file_id) if file_id != 'ELF' else -1
                off = int(offset, 16)
            except ValueError:
                continue
            key = (sid, off, original)
            if key not in translations:
                translations[key] = translated
    return translations


def run_extract_all() -> dict:
    dec_dir = PROJECT_ROOT / 'work' / 'scripts_extraidos'
    if dec_dir.exists() and any(dec_dir.iterdir()):
        return {"success": True, "stdout": ".dec ya existentes, omitiendo extraccion", "stderr": ""}

    extract_all = PROJECT_ROOT / 'tools' / 'extract_all.py'

    result = subprocess.run(
        ["python3", str(extract_all), "--type", "lz77"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_EXTRACT
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr[-500:],
    }


def run_dialogue_order(output_csv: Path, output_json: Path) -> dict:
    dialogue_order = PROJECT_ROOT / 'tools' / 'dialogue_order.py'

    result = subprocess.run(
        ["python3", str(dialogue_order), "--csv", "--json", str(output_json)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_EXTRACT
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr[-500:],
    }


def run_elf_extraction(output_csv: Path) -> dict:
    extract_scripts = PROJECT_ROOT / 'traduccion_tools' / 'extract_dialogue.py'

    result = subprocess.run(
        ["python3", str(extract_scripts), "--elf", "--csv", str(output_csv),
         "--elf-path", str(ORIGINALES / "SLPS_256.11")],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_EXTRACT
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr[-300:],
    }


def _merge_elf_to_csv(elf_csv: Path, target_csv: Path):
    if not elf_csv.exists():
        return
    import csv as csv_mod
    with open(elf_csv, encoding='utf-8-sig', newline='') as ef:
        reader = csv_mod.DictReader(ef)
        elf_rows = list(reader)
    if not elf_rows:
        return
    with open(target_csv, 'a', encoding='utf-8-sig', newline='') as tf:
        writer = csv_mod.writer(tf)
        for row in elf_rows:
            writer.writerow([
                row.get('source', 'ELF'),
                row.get('file_id', 'ELF'),
                row.get('offset', '0x0'),
                row.get('section', '0'),
                row.get('section_order', '0'),
                row.get('original_text', ''),
                row.get('translated_text', ''),
            ])


def parse_csv(csv_path: Path) -> list[dict]:
    rows = []
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('source'):
                continue
            source = row['source'].strip() if row['source'] else 'SCRIPT'
            file_id = (row.get('file_id') or '0').strip()
            offset_str = (row.get('offset') or '0x0').strip()
            original = row.get('original_text') or ''
            translated = (row.get('translated_text') or '').strip()

            section_val = (row.get('section') or '0').strip()
            section = int(section_val) if section_val and section_val != 'None' else 0
            order_val = (row.get('section_order') or '0').strip()
            section_order = int(order_val) if order_val and order_val != 'None' else 0

            try:
                byte_offset = int(offset_str, 16)
            except ValueError:
                continue

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
                'section_id': section,
                'section_order': section_order,
                'original_text': original,
                'translated_text': translated,
            })
    return rows


def _group_translations_by_script(
    existing_entries: list[TextEntry]
) -> tuple[dict, dict]:
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
    sid = new_entry['script_id']
    off = new_entry['byte_offset']
    orig = new_entry['original_text']

    key = (sid, off, orig)
    if key in exact_map:
        stats['matched_exact'] += 1
        return exact_map[key]

    script_entries = by_script.get(sid, [])
    for eoff, eorig, etrans in script_entries:
        if eorig == orig:
            stats['matched_content'] += 1
            return etrans

    for eoff, eorig, etrans in script_entries:
        if abs(eoff - off) <= 16 and _similarity(orig, eorig) > 0.95:
            stats['matched_fuzzy'] += 1
            return etrans

    stats['unmatched'] += 1
    return None


def _compute_script_capacities(script_id: int, entries: list[dict]) -> dict:
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


def _load_json_script_metadata(json_path: Path) -> dict:
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text(encoding='utf-8'))
    meta = {}
    for s in data.get('scripts', []):
        meta[s['script_id']] = {
            'variant': s.get('variant', ''),
            'script_type': s.get('script_type', ''),
            'total_sections': s.get('total_sections', 0),
            'branches': s.get('branches', 0),
        }
    return meta


def import_csv_to_db(csv_path: Path = None) -> dict:
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

    json_path = WORK / 'dialogue_order.json'
    elftemp_csv = WORK / 'build_temp' / '_elf_temp.csv'

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    elftemp_csv.parent.mkdir(parents=True, exist_ok=True)

    extract_result = run_extract_all()
    if not extract_result['success']:
        stats['errors'].append(f"extract_all.py failed: {extract_result['stderr'][-200:]}")
        session.close()
        return stats

    csv_translations = _load_csv_translations(csv_path)

    result = run_dialogue_order(csv_path, json_path)
    if not result['success']:
        stats['errors'].append(f"dialogue_order.py failed: {result['stderr'][-200:]}")
        session.close()
        return stats
    if not csv_path.exists():
        stats['errors'].append("dialogue_order.py no genero el CSV")
        session.close()
        return stats

    elf_result = run_elf_extraction(elftemp_csv)
    if elf_result['success']:
        elf_count = elf_result.get('stdout', '')
        _merge_elf_to_csv(elftemp_csv, csv_path)
    else:
        stats['errors'].append(f"ELF extraction failed: {elf_result['stderr']}")

    script_meta = _load_json_script_metadata(json_path)

    new_rows = parse_csv(csv_path)
    stats['total'] = len(new_rows)

    existing_db = session.query(TextEntry).filter(
        TextEntry.translated_text != ''
    ).all()
    exact_map, by_script = _group_translations_by_script(existing_db)

    csv_new_count = 0
    for key, trans in csv_translations.items():
        if key not in exact_map:
            exact_map[key] = trans
            sid = key[0]
            if sid not in by_script:
                by_script[sid] = []
            by_script[sid].append((key[1], key[2], trans))
            csv_new_count += 1
    stats['csv_preserved'] = csv_new_count

    existing_map = {}
    for e in session.query(TextEntry).all():
        existing_map[(e.script_id, e.byte_offset, e.original_text)] = e

    script_ids_in_csv = set()
    new_entries_by_script = {}
    for row in new_rows:
        sid = row['script_id']
        script_ids_in_csv.add(sid)
        if sid not in new_entries_by_script:
            new_entries_by_script[sid] = []
        new_entries_by_script[sid].append(row)

    existing_scripts = {s.id: s for s in session.query(Script).all()}

    for sid in script_ids_in_csv:
        meta = script_meta.get(sid, {})
        if sid not in existing_scripts:
            script = Script(
                id=sid,
                source='SCRIPT' if sid != -1 else 'ELF',
                variant=meta.get('variant', ''),
                script_type=meta.get('script_type', ''),
                total_sections=meta.get('total_sections', 0),
            )
            session.add(script)
            existing_scripts[sid] = script
        else:
            s = existing_scripts[sid]
            if not s.variant:
                s.variant = meta.get('variant', '')
            if not s.script_type:
                s.script_type = meta.get('script_type', '')
            if not s.total_sections:
                s.total_sections = meta.get('total_sections', 0)

    for sid, entries in new_entries_by_script.items():
        capacities = {}
        if sid != -1:
            try:
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
                if row.get('section_id'):
                    entry.section_id = row['section_id']
                if row.get('section_order'):
                    entry.section_order = row['section_order']
                if entry.translated_text:
                    fit = check_fit(entry.translated_text, entry.source,
                                    cap or entry.original_bytes or 999)
                    entry.fit_status = fit['status']
                    entry.needs_shift = (fit['status'] == 'needs_shift')
                stats['updated'] += 1
            else:
                orig_bytes = len(
                    row['original_text'].encode('utf-16-le')
                    if row['source'] == 'SCRIPT'
                    else row['original_text'].encode('shift-jis')
                )
                entry = TextEntry(
                    script_id=sid,
                    source=row['source'],
                    byte_offset=off,
                    section_id=row.get('section_id', 0),
                    section_order=row.get('section_order', 0),
                    original_text=row['original_text'],
                    translated_text=translated or '',
                    original_bytes=orig_bytes,
                    segment_capacity=cap,
                    is_translated=bool(translated),
                    fit_status='unchecked',
                )
                if translated:
                    fit = check_fit(translated, entry.source,
                                    cap or orig_bytes or 999)
                    entry.fit_status = fit['status']
                    entry.needs_shift = (fit['status'] == 'needs_shift')
                session.add(entry)
                stats['new'] += 1

    for sid, script in existing_scripts.items():
        if sid in script_ids_in_csv:
            script.total_texts = len(new_entries_by_script[sid])
            translated_count = sum(
                1 for row in new_entries_by_script[sid]
                if row.get('translated_text')
            )
            script.translated_texts = max(script.translated_texts or 0, translated_count)

    session.commit()
    session.close()

    return stats

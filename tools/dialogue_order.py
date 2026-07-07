#!/usr/bin/env python3
"""
dialogue_order.py — Extrae textos de scripts .dec usando la firma exacta del
opcode 0x03 y determina el orden narrativo real (secciones y branching).

Fases implementadas:
  Fase 1: find_text_blocks()        — deteccion por firma de 24 bytes (0x03+0x0100)
  Fase 2: parse_pointer_table()     — pointer table para Variante B
  Fase 2b: group_variant_a()        — scene boundaries para Variante A
  Fase 3: resolve_branching()       — deteccion de branching
  Fase 4: exportacion CSV/JSON      — formatos de salida

Uso:
  python dialogue_order.py                          # Procesa todos los SCRIPT_DIALOGUE
  python dialogue_order.py --id 8007                # Solo un script
  python dialogue_order.py --csv                   # Genera CSV enriquecido
  python dialogue_order.py --json analysis.json    # Exporta JSON estructural
  python dialogue_order.py --diagnose              # Modo diagnostico
"""

import argparse
import csv
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Firma unificada: [ANY 8 bytes][0x03][0x0100][8 zeros] = texto en +24
# Los primeros 8 bytes pueden ser ceros, 0x060X010E, o prefijo variable.
TEXT_BLOCK_SIG = bytes(
    [0x03, 0x00, 0x00, 0x00]                            # opcode 0x03
    + [0x00, 0x01, 0x00, 0x00]                            # param 0x0100
    + [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]  # 8 zeros
)
TEXT_BLOCK_HEADER_BEFORE_SIG = 8  # bytes antes de la firma (primera mitad del bloque)

TEXT_START_OFFSET = TEXT_BLOCK_HEADER_BEFORE_SIG + len(TEXT_BLOCK_SIG)  # 24 (bloque completo)

# 16 bytes before a text block that indicate CONTINUATION (same scene)
CONTINUATION_MARKER = bytes(
    [0x06, 0x00, 0x00, 0x00]
    + [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
)

SCRIPT_DIALOGUE_MASK = 0xFFFFFF00
SCRIPT_DIALOGUE_SIG = 0x02000000

DEC_DIR = Path("work/scripts_extraidos")
OUT_CSV = Path("textos/dialogo.csv")
OUT_JSON = Path("work/dialogue_order.json")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    block_offset: int          # offset of the 24-byte signature in .dec
    text_offset: int           # offset of actual text (block_offset + 24)
    text: str                  # decoded UTF-16LE text
    char_count: int            # character count


@dataclass
class PointerEntry:
    entry_offset: int          # offset of this entry in the gap area
    pointer: int               # target offset in bytecode
    count: int                 # count field


@dataclass
class Section:
    section_id: int
    bytecode_start: int        # start offset in bytecode (pointer target or scene boundary)
    texts: list[TextBlock] = field(default_factory=list)
    variants: list[PointerEntry] = field(default_factory=list)   # for branching
    is_branch: bool = False


@dataclass
class ScriptAnalysis:
    script_id: int
    script_type: int           # raw header h0 value
    variant: str               # "A" (gap zeros) or "B" (pointer table)
    total_texts: int
    sections: list[Section] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fase 1: Text block detector
# ---------------------------------------------------------------------------

def find_text_blocks(data: bytes) -> list[TextBlock]:
    """Busca bloques de texto usando firma [ANY 8 bytes][0x03][0x0100][8 zeros]."""
    blocks: list[TextBlock] = []
    seen_text_offsets: set[int] = set()

    pos = 0
    while True:
        pos = data.find(TEXT_BLOCK_SIG, pos)
        if pos == -1:
            break
        
        # La firma empieza a 8 bytes del inicio del bloque (primera mitad variable)
        block_start = pos - TEXT_BLOCK_HEADER_BEFORE_SIG
        if block_start < 0:
            pos += 1
            continue
            
        text_start = block_start + TEXT_START_OFFSET
        if text_start in seen_text_offsets or text_start >= len(data):
            pos += 1
            continue
            
        null_pos = data.find(b'\x00\x00', text_start)
        if null_pos == -1 or null_pos == text_start:
            pos += 1
            continue
            
        try:
            text = data[text_start:null_pos].decode('utf-16-le')
        except UnicodeDecodeError:
            pos = null_pos + 2
            continue
            
        blocks.append(TextBlock(
            block_offset=block_start,
            text_offset=text_start,
            text=text,
            char_count=len(text),
        ))
        seen_text_offsets.add(text_start)
        pos = null_pos + 2
        
    return blocks


# ---------------------------------------------------------------------------
# Fase 2: Variant B — pointer table
# ---------------------------------------------------------------------------

def parse_pointer_table(data: bytes, bytecode_ptr: int) -> list[PointerEntry]:
    """Extrae entradas de la pointer table en el gap area (0x20..bytecode_ptr)."""
    entries: list[PointerEntry] = []
    gap = data[0x20:bytecode_ptr]
    for i in range(0, len(gap), 16):
        chunk = gap[i:i + 16]
        if len(chunk) < 16:
            break
        ptr, cnt, z1, z2 = struct.unpack_from('<IIII', chunk, 0)
        if ptr != 0 or cnt != 0:
            entries.append(PointerEntry(
                entry_offset=0x20 + i,
                pointer=ptr,
                count=cnt,
            ))
    return entries


def _first_block_at_or_after(blocks: list[TextBlock], min_offset: int) -> int | None:
    """Indice del primer bloque con block_offset >= min_offset, o None."""
    for i, tb in enumerate(blocks):
        if tb.block_offset >= min_offset:
            return i
    return None


def group_by_pointers(
    entries: list[PointerEntry],
    text_blocks: list[TextBlock],
    bytecode_ptr: int = 0x2010,
) -> list[Section]:
    """
    Agrupa text blocks en secciones usando los pointer entries como boundaries.

    Los pointer targets son entry points de bytecode (pueden caer en opcodes
    entre text blocks). La primera seccion cubre desde bytecode_ptr hasta el
    primer pointer target; las siguientes desde cada target hasta el siguiente.
    """
    unique_targets = sorted(set(e.pointer for e in entries))
    sorted_blocks = sorted(text_blocks, key=lambda tb: tb.block_offset)

    by_target: dict[int, list[PointerEntry]] = defaultdict(list)
    for e in entries:
        by_target[e.pointer].append(e)

    # Todos los boundaries, incluyendo el inicio del bytecode
    all_boundaries = [bytecode_ptr] + unique_targets

    sections: list[Section] = []
    section_id = 0

    for i, boundary in enumerate(all_boundaries):
        start_idx = _first_block_at_or_after(sorted_blocks, boundary)
        if start_idx is None:
            continue

        next_boundary = all_boundaries[i + 1] if i + 1 < len(all_boundaries) else None
        if next_boundary is not None:
            end_idx = _first_block_at_or_after(sorted_blocks, next_boundary)
            if end_idx is None:
                end_idx = len(sorted_blocks)
        else:
            end_idx = len(sorted_blocks)

        sec_texts = sorted_blocks[start_idx:end_idx]
        if not sec_texts:
            continue

        # Para el preamble (boundary == bytecode_ptr), no hay pointer entries
        if boundary in by_target:
            variants = by_target[boundary]
            is_branch = len(variants) > 1
        else:
            variants = []
            is_branch = False

        sections.append(Section(
            section_id=section_id,
            bytecode_start=boundary,
            texts=sec_texts,
            variants=variants,
            is_branch=is_branch,
        ))
        section_id += 1

    return sections


# ---------------------------------------------------------------------------
# Fase 2b: Variant A — scene boundary detection
# ---------------------------------------------------------------------------

def group_variant_a(text_blocks: list[TextBlock]) -> list[Section]:
    """
    Agrupa text blocks en secciones para Variante A (sin pointer table).

    Regla: una nueva seccion comienza cuando los 16 bytes antes del text block
    NO son el marcador de continuacion (0x06 seguido de 12 ceros).
    El primer text block siempre inicia una nueva seccion.
    """
    sections: list[Section] = []
    section_id = 0
    current_section: Section | None = None

    for tb in text_blocks:
        # No podemos verificar bytes previos para el primer bloque o si no
        # tenemos acceso al .dec — marcamos como nueva seccion por defecto
        # (el caller debe pasar la data para la verificacion real)
        current_section = Section(
            section_id=section_id,
            bytecode_start=tb.block_offset,
            texts=[],
        )
        sections.append(current_section)
        section_id += 1
        current_section.texts.append(tb)

    return sections


def group_variant_a_with_data(data: bytes, text_blocks: list[TextBlock]) -> list[Section]:
    """
    Agrupa text blocks en secciones para Variante A, usando los bytes previos
    a cada bloque para detectar boundaries de escena.
    """
    sections: list[Section] = []
    section_id = 0
    current_section: Section | None = None

    for tb in text_blocks:
        is_new_section = True
        if tb.block_offset >= 16:
            prev_16 = data[tb.block_offset - 16:tb.block_offset]
            if prev_16 == CONTINUATION_MARKER:
                is_new_section = False

        if is_new_section or current_section is None:
            current_section = Section(
                section_id=section_id,
                bytecode_start=tb.block_offset,
                texts=[],
            )
            sections.append(current_section)
            section_id += 1

        current_section.texts.append(tb)

    return sections


# ---------------------------------------------------------------------------
# Fase 3: Branching resolution
# ---------------------------------------------------------------------------

def catalog_counts(scripts: list[ScriptAnalysis]) -> list[int]:
    """Cataloga todos los valores de count observados en pointer entries."""
    counts: set[int] = set()
    for sa in scripts:
        for sec in sa.sections:
            for v in sec.variants:
                counts.add(v.count)
    return sorted(counts)


# ---------------------------------------------------------------------------
# Fase 4: Export
# ---------------------------------------------------------------------------

def export_csv(
    scripts: list[ScriptAnalysis],
    csv_path: Path,
) -> int:
    """Genera CSV enriquecido con columnas section y section_order."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'source', 'file_id', 'offset', 'section', 'section_order',
            'original_text', 'translated_text'
        ])
        for sa in sorted(scripts, key=lambda s: s.script_id):
            source = 'SCRIPT'
            file_id = str(sa.script_id)
            for sec in sa.sections:
                for order, tb in enumerate(sec.texts, start=1):
                    writer.writerow([
                        source,
                        file_id,
                        f"0x{tb.text_offset:05X}",
                        sec.section_id,
                        order,
                        tb.text,
                        '',
                    ])
                    count += 1
    return count


def export_json(scripts: list[ScriptAnalysis], json_path: Path) -> dict:
    """Exporta JSON estructural."""
    result = {
        'total_scripts': len(scripts),
        'total_texts': sum(s.total_texts for s in scripts),
        'variant_a_count': sum(1 for s in scripts if s.variant == 'A'),
        'variant_b_count': sum(1 for s in scripts if s.variant == 'B'),
        'count_catalog': catalog_counts(scripts),
        'scripts': [],
    }
    for sa in sorted(scripts, key=lambda s: s.script_id):
        script_data = {
            'script_id': sa.script_id,
            'script_type': f"0x{sa.script_type:08X}",
            'variant': sa.variant,
            'total_texts': sa.total_texts,
            'total_sections': len(sa.sections),
            'branches': sum(1 for s in sa.sections if s.is_branch),
            'sections': [],
            'diagnostics': sa.diagnostics,
        }
        for sec in sa.sections:
            sec_data = {
                'id': sec.section_id,
                'bytecode_start': f"0x{sec.bytecode_start:06X}",
                'text_count': len(sec.texts),
                'is_branch': sec.is_branch,
                'variants': [{'pointer': f"0x{v.pointer:06X}", 'count': v.count}
                             for v in sec.variants],
                'texts': [
                    {
                        'order': i + 1,
                        'offset': f"0x{tb.text_offset:05X}",
                        'original': tb.text,
                        'char_count': tb.char_count,
                    }
                    for i, tb in enumerate(sec.texts)
                ],
            }
            script_data['sections'].append(sec_data)
        result['scripts'].append(script_data)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


# ---------------------------------------------------------------------------
# Fallback: extraccion heuristica para scripts sin firma 0x03+0x0100
# ---------------------------------------------------------------------------

def _is_jp_codepoint(cp: int) -> bool:
    return (
        0x3040 <= cp <= 0x309F   # Hiragana
        or 0x30A0 <= cp <= 0x30FF  # Katakana
        or 0x4E00 <= cp <= 0x9FFF  # Kanji
        or 0x3000 <= cp <= 0x303F  # CJK Symbols
        or 0xFF00 <= cp <= 0xFFEF  # Fullwidth
        or 0x2000 <= cp <= 0x206F  # General Punctuation
    )


def _is_valid_jp(text: str) -> bool:
    if len(text) < 4:
        return False
    hiragana = sum(1 for c in text if '\u3040' <= c <= '\u309F')
    katakana = sum(1 for c in text if '\u30A0' <= c <= '\u30FF')
    kanji = sum(1 for c in text if '\u4E00' <= c <= '\u9FFF')
    total = hiragana + katakana + kanji
    if total == 0:
        return False
    if total / len(text) < 0.5:
        return False
    if hiragana == 0 and kanji > 0:
        return False
    particles = ['の', 'は', 'が', 'に', 'を', 'て', 'で', 'と', 'か', 'な', 'だ',
                 'し', 'い', 'う', 'る', '？', '！', '、', '。', '「', '」', '…']
    if len(text) <= 8 and (katakana + kanji) == len(text):
        return True
    return any(p in text for p in particles)


def find_text_blocks_fallback(data: bytes) -> list[TextBlock]:
    """Extrae textos via heuristica UTF-16LE (fallback)."""
    blocks: list[TextBlock] = []
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
                except UnicodeDecodeError:
                    i = end
                    continue
                if _is_valid_jp(text):
                    blocks.append(TextBlock(
                        block_offset=start,
                        text_offset=start,
                        text=text,
                        char_count=len(text),
                    ))
                i = end
                continue
            i += 2
    return blocks


# ---------------------------------------------------------------------------
# Core: analizar un script
# ---------------------------------------------------------------------------

def analyze_script(dec_path: Path) -> ScriptAnalysis | None:
    """Analiza un .dec y devuelve su estructura completa."""
    if not dec_path.exists():
        return None

    data = dec_path.read_bytes()
    if len(data) < 0x20:
        return None

    h0 = struct.unpack_from('<I', data, 0)[0]
    if (h0 & SCRIPT_DIALOGUE_MASK) != SCRIPT_DIALOGUE_SIG:
        return None

    sid = int(dec_path.stem.split('_')[1])
    bytecode_ptr = struct.unpack_from('<I', data, 0x10)[0]
    count_field = struct.unpack_from('<I', data, 0x14)[0]

    # Fase 1: detectar text blocks (firma exacta + fallback heuristica fusionados)
    opcode_blocks = find_text_blocks(data)
    fb_blocks = find_text_blocks_fallback(data)

    # Merge: opcode-based tiene preferencia, fallback rellena lo que falta
    seen_offsets = {b.text_offset for b in opcode_blocks}
    text_blocks = list(opcode_blocks)
    for fb in fb_blocks:
        if fb.text_offset not in seen_offsets and fb.text_offset % 2 == 0:
            text_blocks.append(fb)
            seen_offsets.add(fb.text_offset)

    text_blocks.sort(key=lambda b: b.text_offset)
    extraction_method = 'merged'

    # Determinar variante
    gap = data[0x20:bytecode_ptr]
    non_zero = sum(1 for b in gap if b != 0)
    variant = 'B' if non_zero > 0 else 'A'

    # Diagnosticos
    diagnostics = {
        'dec_size': len(data),
        'count_field': count_field,
        'gap_nonzero_bytes': non_zero,
        'text_blocks_opcode': len(text_blocks),
        'extraction_method': extraction_method,
    }

    # Fase 2/2b: agrupar en secciones
    if variant == 'B':
        pointer_entries = parse_pointer_table(data, bytecode_ptr)
        sections = group_by_pointers(pointer_entries, text_blocks, bytecode_ptr)
        diagnostics['pointer_entries'] = len(pointer_entries)
        diagnostics['unique_targets'] = len(set(e.pointer for e in pointer_entries))

        # Pointer entries huerfanos (targets sin textos asociados)
        ptr_targets = set(e.pointer for e in pointer_entries)
        targets_with_texts = {sec.bytecode_start for sec in sections
                              if sec.bytecode_start in ptr_targets and sec.texts}
        diagnostics['orphan_entries'] = len(ptr_targets - targets_with_texts)

        # Textos fuera de seccion (no deberia haber con bytecode_ptr como primer boundary)
        assigned_offsets: set[int] = set()
        for sec in sections:
            for tb in sec.texts:
                assigned_offsets.add(tb.block_offset)
        diagnostics['texts_outside_sections'] = len(
            [tb for tb in text_blocks if tb.block_offset not in assigned_offsets])

    else:
        sections = group_variant_a_with_data(data, text_blocks)
        diagnostics['pointer_entries'] = 0

    return ScriptAnalysis(
        script_id=sid,
        script_type=h0,
        variant=variant,
        total_texts=len(text_blocks),
        sections=sections,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Modo diagnostico
# ---------------------------------------------------------------------------

def run_diagnose(scripts: list[ScriptAnalysis]):
    """Ejecuta modo diagnostico y muestra diferencias vs extract_dialogue.py."""
    print("=" * 60)
    print("MODO DIAGNOSTICO")
    print("=" * 60)

    # Resumen general
    total_texts = sum(s.total_texts for s in scripts)
    total_sections = sum(len(s.sections) for s in scripts)
    total_branches = sum(1 for s in scripts for sec in s.sections if sec.is_branch)
    orphans = sum(s.diagnostics.get('orphan_entries', 0) for s in scripts)
    outside = sum(s.diagnostics.get('texts_outside_sections', 0) for s in scripts)

    print(f"\nScripts SCRIPT_DIALOGUE: {len(scripts)}")
    print(f"  Variant A: {sum(1 for s in scripts if s.variant == 'A')}")
    print(f"  Variant B: {sum(1 for s in scripts if s.variant == 'B')}")
    print(f"Textos totales: {total_texts:,}")
    print(f"Secciones totales: {total_sections}")
    print(f"Branches detectados: {total_branches}")
    print(f"Pointer entries huerfanos: {orphans}")
    print(f"Textos fuera de seccion: {outside}")

    # Catalogo de counts
    counts = catalog_counts(scripts)
    print(f"\nCatalogo de valores 'count' observados: {counts}")

    # Comparacion con extract_dialogue.py (si existe el CSV viejo)
    old_csv = Path("textos/dialogo.csv.bak")
    if old_csv.exists():
        old_texts: dict[tuple[str, str], str] = {}
        with open(old_csv, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row.get('file_id', '').strip(), row.get('offset', '').strip())
                old_texts[key] = row.get('original_text', '')

        new_texts: dict[tuple[str, str], str] = {}
        for sa in scripts:
            for sec in sa.sections:
                for tb in sec.texts:
                    key = (str(sa.script_id), f"0x{tb.text_offset:05X}")
                    new_texts[key] = tb.text

        # Comparar
        only_old = set(old_texts.keys()) - set(new_texts.keys())
        only_new = set(new_texts.keys()) - set(old_texts.keys())
        common = set(old_texts.keys()) & set(new_texts.keys())
        diffs = []
        for key in sorted(common):
            if old_texts[key] != new_texts[key]:
                diffs.append((key, old_texts[key], new_texts[key]))

        print(f"\nComparacion con extract_dialogue.py (dialogo.csv.bak):")
        print(f"  Textos en viejo: {len(old_texts):,}")
        print(f"  Textos en nuevo: {len(new_texts):,}")
        print(f"  Comunes: {len(common):,}")
        print(f"  Solo en viejo (perdidos): {len(only_old)}")
        print(f"  Solo en nuevo (encontrados): {len(only_new)}")
        print(f"  Diferencias de contenido: {len(diffs)}")
        if diffs[:5]:
            print("  Primeras 5 diferencias:")
            for key, old, new in diffs[:5]:
                print(f"    {key[0]} @ {key[1]}:")
                print(f"      viejo: {old!r}")
                print(f"      nuevo: {new!r}")

    # Detalle por script
    print(f"\n{'ID':>6s}  {'Variant':>7s}  {'Texts':>7s}  {'Sections':>8s}  "
          f"{'Branches':>8s}  {'Orphans':>7s}  {'Outside':>7s}")
    print("-" * 60)
    for sa in sorted(scripts, key=lambda s: s.script_id):
        branches = sum(1 for sec in sa.sections if sec.is_branch)
        orphans = sa.diagnostics.get('orphan_entries', 0)
        outside = sa.diagnostics.get('texts_outside_sections', 0)
        print(f"{sa.script_id:>6d}  {sa.variant:>7s}  {sa.total_texts:>7d}  "
              f"{len(sa.sections):>8d}  {branches:>8d}  {orphans:>7d}  {outside:>7d}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Extrae textos y orden narrativo de scripts SCRIPT_DIALOGUE (.dec)'
    )
    parser.add_argument('--id', type=int, default=0,
                        help='Procesar solo un script ID')
    parser.add_argument('--csv', action='store_true',
                        help='Generar CSV enriquecido (textos/dialogo.csv)')
    parser.add_argument('--json', type=str, default=None,
                        help='Exportar JSON estructural')
    parser.add_argument('--diagnose', action='store_true',
                        help='Modo diagnostico: comparar con CSV viejo, reportar huerfanos')
    parser.add_argument('--dec-dir', type=str, default=str(DEC_DIR),
                        help='Directorio de .dec files')
    parser.add_argument('--summary', action='store_true',
                        help='Solo mostrar resumen por script')
    args = parser.parse_args()

    dec_dir = Path(args.dec_dir)
    if not dec_dir.exists():
        print(f"ERROR: {dec_dir} no existe. Ejecuta primero: python tools/extract_all.py --type lz77")
        return 1

    # Encontrar scripts a procesar
    if args.id:
        dec_path = dec_dir / f"ID_{args.id:05d}.dec"
        sa = analyze_script(dec_path)
        if sa is None:
            print(f"ERROR: ID {args.id} no encontrado o no es SCRIPT_DIALOGUE")
            return 1
        scripts = [sa]
    else:
        scripts = []
        for dec_path in sorted(dec_dir.glob('ID_*.dec')):
            if '_rebuilt' in dec_path.stem:
                continue
            sa = analyze_script(dec_path)
            if sa is not None:
                scripts.append(sa)

    if not scripts:
        print("No se encontraron scripts SCRIPT_DIALOGUE.")
        return 1

    # Diagnostico
    if args.diagnose:
        run_diagnose(scripts)
        return 0

    # Summary
    if args.summary:
        print(f"{'ID':>6s}  {'Var':>3s}  {'Texts':>7s}  {'Sections':>8s}  {'Branches':>8s}")
        print("-" * 45)
        for sa in sorted(scripts, key=lambda s: s.script_id):
            branches = sum(1 for sec in sa.sections if sec.is_branch)
            print(f"{sa.script_id:>6d}  {sa.variant:>3s}  {sa.total_texts:>7d}  "
                  f"{len(sa.sections):>8d}  {branches:>8d}")
        total = sum(s.total_texts for s in scripts)
        print(f"\nTotal: {len(scripts)} scripts, {total:,} textos")
        return 0

    # CSV
    if args.csv:
        count = export_csv(scripts, OUT_CSV)
        print(f"CSV enriquecido: {OUT_CSV} ({count:,} textos)")

    # JSON
    if args.json:
        json_path = Path(args.json)
        export_json(scripts, json_path)
        print(f"JSON estructural: {json_path}")

    # Default: summary + export both
    if not args.csv and not args.json and not args.diagnose and not args.summary:
        count = export_csv(scripts, OUT_CSV)
        json_path = OUT_JSON
        export_json(scripts, json_path)
        print(f"CSV: {OUT_CSV} ({count:,} textos)")
        print(f"JSON: {json_path}")
        print(f"Scripts: {len(scripts)} | Variant A: {sum(1 for s in scripts if s.variant == 'A')} | "
              f"Variant B: {sum(1 for s in scripts if s.variant == 'B')}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())

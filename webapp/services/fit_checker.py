"""Fit-checker — valida si una traduccion cabe en el espacio disponible."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / 'tools'))

from glyph_map import encode_game_utf16, encode_game_sjis


def check_fit(translated_text: str, source: str, capacity: int) -> dict:
    """Verifica si la traduccion cabe en el espacio disponible.

    Returns dict with:
        status: 'ok' | 'tight' | 'needs_shift' | 'unchecked'
        used_bytes: bytes ocupados
        capacity: bytes disponibles
        remaining: bytes sobrantes (negativo = no cabe)
    """
    if not translated_text.strip():
        return {
            'status': 'unchecked',
            'used_bytes': 0,
            'capacity': capacity,
            'remaining': capacity,
        }

    if source == 'SCRIPT':
        encoded = encode_game_utf16(translated_text)
    else:
        encoded = encode_game_sjis(translated_text)

    # +2 for null terminator in SCRIPT (UTF-16LE)
    used = len(encoded) + (2 if source == 'SCRIPT' else 0)
    remaining = capacity - used

    if remaining >= 20:
        status = 'ok'
    elif remaining >= 0:
        status = 'tight'
    else:
        status = 'needs_shift'

    return {
        'status': status,
        'used_bytes': used,
        'capacity': capacity,
        'remaining': remaining,
    }


def batch_check_fit(entries: list, session) -> int:
    """Revalida el fit de una lista de entries. Retorna cuantas cambiaron."""
    from ..database import TextEntry
    changed = 0
    for entry in entries:
        if not entry.translated_text:
            continue
        result = check_fit(
            entry.translated_text,
            entry.source,
            entry.segment_capacity or entry.original_bytes or 999
        )
        if result['status'] != entry.fit_status:
            entry.fit_status = result['status']
            entry.needs_shift = (result['status'] == 'needs_shift')
            changed += 1
    if changed:
        session.commit()
    return changed

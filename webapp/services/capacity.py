"""Calcula segment capacity para textos SCRIPT usando script_rebuilder."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / 'tools'))

from script_rebuilder import find_segment_containing, TextSegment


def compute_capacity(dec_data: bytes, byte_offset: int, original_text: str) -> int:
    """Calcula la capacidad en bytes del segmento que contiene byte_offset.

    Returns total bytes disponibles (texto + null + padding).
    """
    try:
        segment = find_segment_containing(dec_data, byte_offset, original_text)
        return segment.capacity_bytes
    except Exception:
        return 0


def compute_all_capacities(script_id: int, entries: list) -> dict:
    """Calcula capacidades para todas las entries de un script.

    entries: lista de dicts con byte_offset y original_text.
    Retorna dict: byte_offset -> capacity.
    """
    dec_path = PROJECT_ROOT / 'work' / 'scripts_extraidos' / f'ID_{script_id:05d}.dec'
    if not dec_path.exists():
        return {}

    dec_data = dec_path.read_bytes()
    capacities = {}

    for entry in entries:
        off = entry.get('byte_offset', entry.get('offset', 0))
        orig = entry.get('original_text', '')
        try:
            cap = compute_capacity(dec_data, off, orig)
            capacities[off] = cap
        except Exception:
            capacities[off] = 0

    return capacities

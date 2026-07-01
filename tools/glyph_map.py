#!/usr/bin/env python3
"""
glyph_map.py — Fuente unica de verdad para el mapeo espanol → cirilico.

Importado por: script_rebuilder.py, apply_translation.py, webapp/services/fit_checker.py
"""

SPANISH_TO_GLYPH = {
    'á': '\u0413',  # Г
    'é': '\u0414',  # Д
    'í': '\u0415',  # Е
    'ó': '\u0416',  # Ж
    'ú': '\u0417',  # З
    'ñ': '\u0418',  # И
    'Ñ': '\u0419',  # Й
    '¡': '\u041A',  # К
    '¿': '\u041B',  # Л
    'Á': '\u0413',  # Г (sin mayuscula distinta)
    'É': '\u0414',  # Д
    'Í': '\u0415',  # Е
    'Ó': '\u0416',  # Ж
    'Ú': '\u0417',  # З
    'Ü': '\u0417',  # З (aproximacion)
    'ü': '\u0417',  # З
}


def game_string(text: str) -> str:
    """Convierte espanol legible a los glifos disponibles del juego."""
    return ''.join(SPANISH_TO_GLYPH.get(ch, ch) for ch in text)


def encode_game_utf16(text: str) -> bytes:
    """Codifica texto espanol a UTF-16LE con mapeo cirilico."""
    return game_string(text).encode('utf-16-le')


def encode_game_sjis(text: str) -> bytes:
    """Codifica texto espanol a Shift-JIS con mapeo cirilico."""
    return game_string(text).encode('shift-jis')

"""
Glyph map: converts non-ASCII characters to Cyrillic glyphs available in the
game's Japanese font. Each target language defines its own mapping.

Imported by: script_rebuilder.py, apply_translation.py, webapp/services/fit_checker.py

Encoding functions accept an optional glyph_map; if omitted, ES_MAP is used
for backwards compatibility with all existing callers.
"""

ES_MAP = {
    'á': '\u0413',  # Г
    'é': '\u0414',  # Д
    'í': '\u0415',  # Е
    'ó': '\u0416',  # Ж
    'ú': '\u0417',  # З
    'ñ': '\u0418',  # И
    'Ñ': '\u0419',  # Й
    '¡': '\u041A',  # К
    '¿': '\u041B',  # Л
    'Á': '\u0413',  # Г
    'É': '\u0414',  # Д
    'Í': '\u0415',  # Е
    'Ó': '\u0416',  # Ж
    'Ú': '\u0417',  # З
    'Ü': '\u0417',  # З
    'ü': '\u0417',  # З
    '♥': '＠',      # Native game glyph used as heart in original text
    '♡': '＠',
    '❤': '＠',
    '@': '＠',
}

EN_MAP = {}

AVAILABLE_GLYPHS = [
    ('\u0410', 'U+0410', 'А'),   # Cyrillic A
    ('\u0411', 'U+0411', 'Б'),   # Be
    ('\u0412', 'U+0412', 'В'),   # Ve
    ('\u0413', 'U+0413', 'Г'),   # Ghe — used: á, Á
    ('\u0414', 'U+0414', 'Д'),   # De — used: é, É
    ('\u0415', 'U+0415', 'Е'),   # Ie — used: í, Í
    ('\u0416', 'U+0416', 'Ж'),   # Zhe — used: ó, Ó
    ('\u0417', 'U+0417', 'З'),   # Ze — used: ú, Ú, ü, Ü
    ('\u0418', 'U+0418', 'И'),   # I — used: ñ
    ('\u0419', 'U+0419', 'Й'),   # Short I — used: Ñ
    ('\u041A', 'U+041A', 'К'),   # Ka — used: ¡
    ('\u041B', 'U+041B', 'Л'),   # El — used: ¿
    ('\u041C', 'U+041C', 'М'),   # Em
    ('\u041D', 'U+041D', 'Н'),   # En
    ('\u041E', 'U+041E', 'О'),   # O
    ('\u041F', 'U+041F', 'П'),   # Pe
    ('\u0420', 'U+0420', 'Р'),   # Er
    ('\u0421', 'U+0421', 'С'),   # Es
    ('\u0422', 'U+0422', 'Т'),   # Te
    ('\u0423', 'U+0423', 'У'),   # U
    ('\u0424', 'U+0424', 'Ф'),   # Ef
    ('\u0425', 'U+0425', 'Х'),   # Ha
    ('\u0426', 'U+0426', 'Ц'),   # Tse
    ('\u0427', 'U+0427', 'Ч'),   # Che
    ('\u0428', 'U+0428', 'Ш'),   # Sha
    ('\u0429', 'U+0429', 'Щ'),   # Shcha
    ('\u042A', 'U+042A', 'Ъ'),   # Hard sign
    ('\u042B', 'U+042B', 'Ы'),   # Yeru
    ('\u042C', 'U+042C', 'Ь'),   # Soft sign
    ('\u042D', 'U+042D', 'Э'),   # E
    ('\u042E', 'U+042E', 'Ю'),   # Yu
    ('\u042F', 'U+042F', 'Я'),   # Ya
    ('\u0430', 'U+0430', 'а'),   # Cyrillic small a
    ('\u0431', 'U+0431', 'б'),
    ('\u0432', 'U+0432', 'в'),
    ('\u0433', 'U+0433', 'г'),
    ('\u0434', 'U+0434', 'д'),
    ('\u0435', 'U+0435', 'е'),
    ('\u0436', 'U+0436', 'ж'),
    ('\u0437', 'U+0437', 'з'),
    ('\u0438', 'U+0438', 'и'),
    ('\u0439', 'U+0439', 'й'),
    ('\u043A', 'U+043A', 'к'),
    ('\u043B', 'U+043B', 'л'),
    ('\u043C', 'U+043C', 'м'),
    ('\u043D', 'U+043D', 'н'),
    ('\u043E', 'U+043E', 'о'),
    ('\u043F', 'U+043F', 'п'),
    ('\u0440', 'U+0440', 'р'),
    ('\u0441', 'U+0441', 'с'),
    ('\u0442', 'U+0442', 'т'),
    ('\u0443', 'U+0443', 'у'),
    ('\u0444', 'U+0444', 'ф'),
    ('\u0445', 'U+0445', 'х'),
    ('\u0446', 'U+0446', 'ц'),
    ('\u0447', 'U+0447', 'ч'),
    ('\u0448', 'U+0448', 'ш'),
    ('\u0449', 'U+0449', 'щ'),
    ('\u044A', 'U+044A', 'ъ'),
    ('\u044B', 'U+044B', 'ы'),
    ('\u044C', 'U+044C', 'ь'),
    ('\u044D', 'U+044D', 'э'),
    ('\u044E', 'U+044E', 'ю'),
    ('\u044F', 'U+044F', 'я'),
]

SPANISH_TO_GLYPH = ES_MAP


def get_glyph_map(target_lang: str = "es") -> dict:
    if target_lang == "en":
        return dict(EN_MAP)
    elif target_lang == "es":
        return dict(ES_MAP)
    return dict(ES_MAP)


def game_string(text: str, glyph_map: dict | None = None) -> str:
    if glyph_map is None:
        glyph_map = ES_MAP
    if not glyph_map:
        return text
    return ''.join(glyph_map.get(ch, ch) for ch in text)


def encode_game_utf16(text: str, glyph_map: dict | None = None) -> bytes:
    return game_string(text, glyph_map).encode('utf-16-le')


def encode_game_sjis(text: str, glyph_map: dict | None = None) -> bytes:
    return game_string(text, glyph_map).encode('shift-jis')

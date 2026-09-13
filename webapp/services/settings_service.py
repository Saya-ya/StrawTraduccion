import json

from sqlalchemy.exc import DatabaseError

from ..database import get_session, Setting


def get_setting(key: str, default=None):
    session = get_session()
    try:
        row = session.query(Setting).filter(Setting.key == key).first()
    except DatabaseError:
        return default
    finally:
        session.close()
    if row is None:
        return default
    if row.value is None or row.value == "":
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return row.value


def set_setting(key: str, value) -> None:
    session = get_session()
    try:
        row = session.query(Setting).filter(Setting.key == key).first()
        serialized = json.dumps(value, ensure_ascii=False)
        if row is None:
            row = Setting(key=key, value=serialized)
            session.add(row)
        else:
            row.value = serialized
        session.commit()
    finally:
        session.close()


def normalize_glyph_map(value) -> dict:
    """Return glyph maps in encoder format: source character -> game glyph."""
    if not isinstance(value, dict):
        return {}

    import sys
    from pathlib import Path

    tools_path = str(Path(__file__).parent.parent.parent / "tools")
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    from glyph_map import AVAILABLE_GLYPHS

    available = {glyph for glyph, _codepoint, _label in AVAILABLE_GLYPHS}
    normalized = {}
    for key, val in value.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        if len(key) != 1 or len(val) != 1:
            continue
        if key in available and val not in available:
            normalized[val] = key
        else:
            normalized[key] = val
    return normalized


def invert_glyph_map(value) -> dict:
    """Return glyph maps in UI format: game glyph -> source character."""
    inverted = {}
    for source, glyph in normalize_glyph_map(value).items():
        inverted.setdefault(glyph, source)
    return inverted


def load_glyph_map() -> dict:
    import sys
    from pathlib import Path

    target_lang = get_setting("target_lang", "es")

    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
    from glyph_map import ES_MAP, get_glyph_map as _get_glyph_map

    if target_lang == "en":
        return {}
    elif target_lang == "custom":
        return normalize_glyph_map(get_setting("custom_glyph_map", {}))
    else:
        return dict(ES_MAP)

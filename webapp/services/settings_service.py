import json

from ..database import get_session, Setting


def get_setting(key: str, default=None):
    session = get_session()
    row = session.query(Setting).filter(Setting.key == key).first()
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
    row = session.query(Setting).filter(Setting.key == key).first()
    serialized = json.dumps(value, ensure_ascii=False)
    if row is None:
        row = Setting(key=key, value=serialized)
        session.add(row)
    else:
        row.value = serialized
    session.commit()
    session.close()


def load_glyph_map() -> dict:
    import sys
    from pathlib import Path

    target_lang = get_setting("target_lang", "es")

    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
    from glyph_map import ES_MAP, get_glyph_map as _get_glyph_map

    if target_lang == "en":
        return {}
    elif target_lang == "custom":
        return get_setting("custom_glyph_map", {})
    else:
        return dict(ES_MAP)

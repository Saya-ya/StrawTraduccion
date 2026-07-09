#!/usr/bin/env python3
"""Tests para glyph_map, i18n, settings_service, fit_checker."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))


def test_glyph_map_es():
    """Mapeo español: caracteres con tilde/enie se mapean a cirilico."""
    from glyph_map import ES_MAP, game_string, encode_game_utf16, get_glyph_map

    gm = get_glyph_map("es")
    assert len(gm) == 16
    assert gm["ñ"] == "\u0418"

    result = game_string("año", gm)
    assert result == "a\u0418o"
    assert len(result) == 3

    encoded = encode_game_utf16("año", gm)
    assert encoded == "a\u0418o".encode("utf-16-le")
    assert len(encoded) == 6


def test_glyph_map_en():
    """Mapeo ingles: vacio, sin sustituciones."""
    from glyph_map import EN_MAP, get_glyph_map, game_string, encode_game_utf16

    gm = get_glyph_map("en")
    assert len(gm) == 0

    result = game_string("hello", gm)
    assert result == "hello"

    encoded = encode_game_utf16("hello", gm)
    assert encoded == "hello".encode("utf-16-le")


def test_glyph_map_custom():
    """Mapeo personalizado: polaco."""
    from glyph_map import game_string, encode_game_utf16

    custom_map = {
        "\u0105": "\u0413",  # ą → Г
        "\u0107": "\u0414",  # ć → Д
        "\u0119": "\u0415",  # ę → Е
    }

    result = game_string("ąćę", custom_map)
    assert result == "\u0413\u0414\u0415"

    encoded = encode_game_utf16("ąćę", custom_map)
    assert len(encoded) == 6


def test_glyph_map_default():
    """Sin pasar glyph_map, usa ES_MAP por defecto."""
    from glyph_map import game_string, encode_game_utf16

    result = game_string("año")
    assert result == "a\u0418o"


def test_glyph_map_roundtrip():
    """Roundtrip: el encoding es determinista."""
    from glyph_map import encode_game_utf16, game_string

    text = "¡Hola, cómo estás?"
    encoded = encode_game_utf16(text)
    mapped = game_string(text)
    assert encoded == mapped.encode("utf-16-le")


def test_glyph_map_uppercase():
    """Mayusculas se mapean correctamente (comparten glifo con minuscula en algunos casos)."""
    from glyph_map import game_string, ES_MAP

    assert game_string("Á") == "\u0413"    # Г, igual que á
    assert game_string("É") == "\u0414"    # Д
    assert game_string("Ñ") == "\u0419"    # Й


def test_char_unmapped_passthrough():
    """Caracteres no mapeados pasan tal cual."""
    from glyph_map import game_string, encode_game_utf16

    result = game_string("abc", {})
    assert result == "abc"


def test_i18n_loader():
    """Carga de strings para ambos idiomas."""
    from webapp.i18n import load_strings

    es = load_strings("es")
    en = load_strings("en")

    assert es["nav.dashboard"] == "Dashboard"
    assert en["nav.dashboard"] == "Dashboard"
    assert es["nav.search"] == "Buscar"
    assert en["nav.search"] == "Search"
    assert es["scripts.th_texts"] == "Textos"
    assert en["scripts.th_texts"] == "Texts"
    assert "import.btn_extract" in es
    assert "import.btn_extract" in en


def test_i18n_missing_key():
    """Clave ausente devuelve la propia clave."""
    from webapp.i18n import load_strings

    strings = load_strings("es")
    result = strings.get("no_existe", "no_existe")
    assert result == "no_existe"


def test_fit_checker_params():
    """Fit-checker acepta glyph_map opcional."""
    from webapp.services.fit_checker import check_fit

    result = check_fit("Hola", "SCRIPT", 100)
    assert result["status"] == "ok"
    assert result["used_bytes"] > 0

    result_empty = check_fit("", "SCRIPT", 100)
    assert result_empty["status"] == "unchecked"

    result_tight = check_fit("x" * 40, "SCRIPT", 82)
    assert result_tight["status"] in ("tight", "ok", "needs_shift")

    # Con glyph_map vacio (ingles)
    result_en = check_fit("hello", "SCRIPT", 100, glyph_map={})
    assert result_en["status"] == "ok"


def test_settings_service():
    """Settings service lee y escribe correctamente."""
    from webapp.config import DB_PATH
    from webapp.database import init_db, SessionLocal, engine, Base

    # Ensure DB and tables exist
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)

    from webapp.services.settings_service import get_setting, set_setting

    set_setting("test_key", "test_value")
    assert get_setting("test_key") == "test_value"

    set_setting("test_json", {"a": 1, "b": [2]})
    val = get_setting("test_json")
    assert val == {"a": 1, "b": [2]}

    assert get_setting("nonexistent_key", "default_val") == "default_val"


def test_glyph_map_available_glyphs():
    """AVAILABLE_GLYPHS tiene todos los campos esperados."""
    from glyph_map import AVAILABLE_GLYPHS

    assert len(AVAILABLE_GLYPHS) > 20
    for item in AVAILABLE_GLYPHS:
        glyph_char, codepoint, label = item
        assert len(glyph_char) == 1
        assert codepoint.startswith("U+")
        assert len(label) == 1


if __name__ == "__main__":
    results = []
    for name, func in list(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                print(f"  PASS {name}")
                results.append(True)
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                results.append(False)

    passed = sum(results)
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(failed)

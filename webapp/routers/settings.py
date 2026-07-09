import json

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR
from ..i18n import inject_i18n
from ..services.settings_service import get_setting, set_setting

router = APIRouter(prefix="/settings", tags=["settings"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    ctx = dict(getattr(request.state, "i18n", inject_i18n(request)))
    ctx.update(kwargs)
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **ctx))


@router.post("/ui-lang")
async def set_ui_lang(request: Request, ui_lang: str = Form("")):
    """Changes the UI language."""
    if ui_lang in ("es", "en"):
        set_setting("ui_lang", ui_lang)
        response = JSONResponse({"ok": True})
        response.set_cookie("ui_lang", ui_lang, max_age=60 * 60 * 24 * 365)
        return response
    return JSONResponse({"error": "Invalid language"}, status_code=400)


@router.post("/target-lang")
async def set_target_lang(request: Request, target_lang: str = Form("")):
    if target_lang in ("es", "en", "custom"):
        set_setting("target_lang", target_lang)
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Invalid target language"}, status_code=400)


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
    from glyph_map import AVAILABLE_GLYPHS, ES_MAP

    target_lang = get_setting("target_lang", "es")
    custom_map = get_setting("custom_glyph_map", {})

    if target_lang == "es":
        current_map = dict(ES_MAP)
    elif target_lang == "custom":
        current_map = dict(custom_map)
    else:
        current_map = {}

    glyph_rows = []
    for char, codepoint, label in AVAILABLE_GLYPHS:
        glyph_rows.append({
            "char": char,
            "codepoint": codepoint,
            "label": label,
            "mapped_to": current_map.get(char, ""),
        })

    return render("config.html", request,
        target_lang=target_lang,
        glyph_rows=glyph_rows)


@router.get("/glyphs", response_class=HTMLResponse)
def glyph_config(request: Request):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))
    from glyph_map import AVAILABLE_GLYPHS, ES_MAP

    target_lang = get_setting("target_lang", "es")
    custom_map = get_setting("custom_glyph_map", {})

    if target_lang == "es":
        current_map = dict(ES_MAP)
        locked = True
    elif target_lang == "custom":
        current_map = dict(custom_map)
        locked = False
    else:
        current_map = {}
        locked = True

    glyph_rows = []
    for char, codepoint, label in AVAILABLE_GLYPHS:
        glyph_rows.append({
            "char": char,
            "codepoint": codepoint,
            "label": label,
            "mapped_to": current_map.get(char, ""),
        })

    return render("glyph_config.html", request,
        target_lang=target_lang,
        locked=locked,
        glyph_rows=glyph_rows,
    )


@router.post("/glyphs")
async def save_glyph_map(request: Request):
    target_lang = get_setting("target_lang", "es")
    if target_lang != "custom":
        return JSONResponse({"error": "Solo editable en modo personalizado"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON invalido"}, status_code=400)

    validated = {}
    for glyph, target_char in body.items():
        if not isinstance(target_char, str) or len(target_char) != 1:
            continue
        try:
            target_char.encode("latin-1")
        except UnicodeEncodeError:
            continue
        validated[glyph] = target_char

    set_setting("custom_glyph_map", validated)
    return JSONResponse({"ok": True})

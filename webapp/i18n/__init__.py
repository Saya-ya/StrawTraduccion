import importlib


def load_strings(lang: str = "es") -> dict:
    if lang not in ("es", "en"):
        lang = "es"
    mod = importlib.import_module(f"webapp.i18n.{lang}")
    return getattr(mod, "STRINGS", {})


def get_ui_lang(request) -> str:
    cookie_lang = request.cookies.get("ui_lang", "")
    if cookie_lang in ("es", "en"):
        return cookie_lang

    from ..services.settings_service import get_setting
    db_lang = get_setting("ui_lang", "es")
    if db_lang in ("es", "en"):
        return db_lang
    return "es"


def inject_i18n(request) -> dict:
    lang = get_ui_lang(request)
    strings = load_strings(lang)

    from ..services.settings_service import get_setting
    target_lang = get_setting("target_lang", "es")

    def _(key: str, **kwargs) -> str:
        val = strings.get(key, key)
        if kwargs:
            return val.format(**kwargs)
        return val

    return {"_": _, "ui_lang": lang, "i18n_strings": strings}

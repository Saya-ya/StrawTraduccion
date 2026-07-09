import sys
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import JSONResponse, HTMLResponse

from ..database import get_session, TextEntry
from ..services.fit_checker import check_fit
from ..services.settings_service import load_glyph_map
from ..i18n import inject_i18n

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from docs.legado.fix_linebreaks import find_break_proportions, insert_breaks_at_proportions

router = APIRouter(prefix="/api/texts", tags=["texts"])


def _get_username(request: Request) -> str:
    return request.cookies.get("username", "anonymous")


def _get_i18n(request: Request):
    return getattr(request.state, "i18n", inject_i18n(request))


@router.get("/{entry_id}/edit", response_class=HTMLResponse)
def get_editor(request: Request, entry_id: int):
    _ = _get_i18n(request)["_"]
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "Texto no encontrado")

    if entry.locked_by and entry.locked_at:
        try:
            stored = entry.locked_at
            if stored.tzinfo is not None:
                stored = stored.replace(tzinfo=None)
            lock_age = (datetime.utcnow() - stored).total_seconds()
        except Exception:
            lock_age = 999
        if lock_age < 60:
            session.close()
            return HTMLResponse(
                f'<div class="text-yellow-400 text-sm">'
                f'{_("editor.locked_by")} {entry.locked_by}</div>'
            )

    entry.locked_by = "user"
    entry.locked_at = datetime.utcnow()

    translated = entry.translated_text or ''
    original = entry.original_text or ''
    if translated and original and '\r\n' in original and '\n' not in translated:
        proportions = find_break_proportions(original)
        if proportions:
            fixed_text = insert_breaks_at_proportions(translated, proportions)
            if fixed_text != translated:
                translated = fixed_text
                entry.translated_text = fixed_text

    session.commit()

    line_count = max(3, translated.count('\n') + 1)
    row_height = min(max(line_count, 3), 20)
    capacity = entry.segment_capacity or 999

    html = f'''<div class="text-entry border border-gray-700 rounded p-3 bg-gray-700/40"
                 id="entry-{entry_id}">
    <form hx-put="/api/texts/{entry_id}"
          hx-target="#entry-{entry_id}"
          hx-swap="outerHTML"
          class="space-y-2"
          id="editor-form-{entry_id}">
        <div class="text-xs text-gray-500 font-mono mb-1">
            [{entry.section_id}:{entry.section_order}] 0x{entry.byte_offset:05X} (#{entry_id})
        </div>
        <div class="text-sm text-gray-400 mb-1 whitespace-pre-wrap">{entry.original_text[:300]}</div>
        <textarea name="translated_text"
                  class="w-full bg-gray-800 text-white rounded p-2 text-sm focus:outline-none focus:ring-2 focus:ring-pink-500"
                  id="editor-ta-{entry_id}"
                  rows="{row_height}">{translated}</textarea>
        <div class="flex gap-2 text-xs items-center">
            <span id="byte-counter-{entry_id}" class="text-gray-400 font-mono">
                bytes: --
            </span>
        </div>
        <div class="flex gap-2 text-xs">
            <button type="submit" class="bg-pink-600 px-3 py-1 rounded hover:bg-pink-700 font-bold">
                {_('editor.save')} ({_('editor.save_hint')})
            </button>
            <button type="button"
                    id="cancel-btn-{entry_id}"
                    class="bg-gray-600 px-3 py-1 rounded hover:bg-gray-500"
                    hx-get="/api/texts/{entry_id}/row"
                    hx-target="#entry-{entry_id}"
                    hx-swap="outerHTML">
                {_('editor.cancel')}
            </button>
        </div>
    </form>
    <script>
        (function() {{
            var ta = document.getElementById('editor-ta-{entry_id}');
            var cancelBtn = document.getElementById('cancel-btn-{entry_id}');
            var counter = document.getElementById('byte-counter-{entry_id}');
            var capacity = {capacity};
            if (!ta) return;
            
            function updateCounter() {{
                var bytes = ta.value.length * 2 + 2;  // UTF-16LE + null
                var remaining = capacity - bytes;
                var color, label;
                if (remaining >= 20) {{ color = '#4ade80'; label = '{_("editor.bytes_free")}'; }}
                else if (remaining >= 0) {{ color = '#facc15'; label = '{_("editor.bytes_tight")}'; }}
                else {{ color = '#f87171'; label = '{_("editor.bytes_exceeded")}'; }}
                counter.style.color = color;
                counter.textContent = bytes + ' / ' + capacity + ' bytes (' + (remaining > 0 ? '+' : '') + remaining + ' ' + label + ')';
            }}
            updateCounter();
            ta.addEventListener('input', updateCounter);
            ta.addEventListener('keydown', function(e) {{
                if (e.ctrlKey && e.key === 'Enter') {{
                    e.preventDefault();
                    this.form.requestSubmit();
                }}
                if (e.key === 'Escape') {{
                    e.preventDefault();
                    htmx.trigger(cancelBtn, 'click');
                }}
            }});
            ta.focus();
            ta.setSelectionRange(ta.value.length, ta.value.length);
        }})();
    </script>
</div>'''
    session.close()
    return HTMLResponse(html)


@router.put("/{entry_id}")
def update_text(request: Request, entry_id: int, translated_text: str = Form(...)):
    _ = _get_i18n(request)["_"]
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "Texto no encontrado")

    entry.translated_text = translated_text
    entry.is_translated = bool(translated_text.strip())
    entry.updated_at = datetime.now(timezone.utc)
    entry.locked_by = ""
    entry.locked_at = None

    capacity = entry.segment_capacity or entry.original_bytes or 999
    glyph_map = load_glyph_map()
    fit = check_fit(translated_text, entry.source, capacity, glyph_map=glyph_map)
    entry.fit_status = fit['status']
    entry.needs_shift = (fit['status'] == 'needs_shift')

    script = entry.script
    if script:
        script.translated_texts = session.query(TextEntry).filter(
            TextEntry.script_id == script.id,
            TextEntry.is_translated == True
        ).count()

    session.commit()

    return _render_entry_row(entry, fit, _)


def _render_entry_row(entry, fit: dict, _=None) -> HTMLResponse:
    if _ is None:
        from ..i18n import load_strings, get_ui_lang
        def _(key, **kw): return load_strings("es").get(key, key).format(**kw)

    fit_icon = {'ok': '🟢', 'tight': '🟡', 'needs_shift': '🔴'}.get(fit['status'], '⚪')
    bg = 'bg-green-900/30' if entry.is_translated else 'bg-gray-800'
    remaining = fit.get('remaining', 0)
    cap_info = f'{remaining} {_("editor.bytes_free")}' if remaining >= 0 else f'{abs(remaining)} {_("editor.bytes_exceeded")}'

    translated_block = ''
    if entry.translated_text:
        translated_block = (
            f'<div class="text-sm text-white bg-gray-700/50 rounded p-2">'
            f'{entry.translated_text[:200]}</div>'
        )

    sec_info = f'[{entry.section_id}:{entry.section_order}] '

    html = f'''<div class="text-entry border border-gray-700 rounded p-3 cursor-pointer {bg}"
        id="entry-{entry.id}"
        hx-get="/api/texts/{entry.id}/edit"
        hx-target="closest .text-entry"
        hx-swap="outerHTML">
    <div class="flex gap-3">
        <div class="flex-1 min-w-0">
            <div class="text-xs text-gray-500 font-mono mb-1">
                {sec_info}0x{entry.byte_offset:05X} (#{entry.id})
                {f'<span class="text-green-400 ml-2">✓</span>' if entry.is_translated else ''}
                {f'<span class="text-red-400 ml-2">⚠</span>' if fit['status'] == 'needs_shift' else ''}
            </div>
            <div class="text-sm text-gray-300 mb-2">{entry.original_text[:120]}</div>
            {translated_block if translated_block else f'<div class="text-xs text-gray-600 italic">{_("editor.click_translate")}</div>'}
        </div>
        <div class="text-xs flex-shrink-0" title="{cap_info}">{fit_icon}</div>
    </div>
</div>'''
    return HTMLResponse(html)


@router.get("/{entry_id}/row", response_class=HTMLResponse)
def get_row(request: Request, entry_id: int):
    _ = _get_i18n(request)["_"]
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "No encontrado")

    entry.locked_by = ""
    entry.locked_at = None
    session.commit()

    fit_icon = {'ok': '🟢', 'tight': '🟡', 'needs_shift': '🔴'}.get(entry.fit_status, '⚪')
    translated_html = ''
    if entry.translated_text:
        translated_html = f'<div class="text-sm text-white bg-gray-700/50 rounded p-2">{entry.translated_text[:200]}</div>'

    sec_info = f'[{entry.section_id}:{entry.section_order}] '
    bg = 'bg-green-900/30' if entry.is_translated else 'bg-gray-800'
    html = f'''<div class="text-entry border border-gray-700 rounded p-3 {bg} cursor-pointer"
                  id="entry-{entry_id}"
                  hx-get="/api/texts/{entry_id}/edit"
                  hx-target="closest .text-entry"
                  hx-swap="outerHTML">
        <div class="flex gap-3">
            <div class="flex-1 min-w-0">
                <div class="text-xs text-gray-500 font-mono mb-1">
                    {sec_info}0x{entry.byte_offset:05X} (#{entry_id})
                    {f'<span class="text-green-400 ml-2">✓</span>' if entry.is_translated else ''}
                    {f'<span class="text-red-400 ml-2">⚠</span>' if entry.needs_shift else ''}
                </div>
                <div class="text-sm text-gray-300 mb-2">{entry.original_text[:120]}</div>
                {translated_html if translated_html else f'<div class="text-xs text-gray-600 italic">{_("editor.click_translate")}</div>'}
            </div>
            <div class="text-xs">{fit_icon}</div>
        </div>
    </div>'''
    session.close()
    return HTMLResponse(html)

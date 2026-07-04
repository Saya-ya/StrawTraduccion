"""API REST para textos: editar, lock, fit-check."""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from ..database import get_session, TextEntry
from ..services.fit_checker import check_fit

router = APIRouter(prefix="/api/texts", tags=["texts"])


class TranslationUpdate(BaseModel):
    translated_text: str
    username: str = "anonymous"


def _get_username(request: Request) -> str:
    """Obtiene username del cookie o query param."""
    return request.cookies.get("username", "anonymous")


@router.get("/{entry_id}/edit", response_class=HTMLResponse)
def get_editor(entry_id: int):
    """Devuelve el fragmento HTML del editor inline."""
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "Texto no encontrado")

    if entry.locked_by and entry.locked_at:
        # locked_at is stored as naive UTC; compare with utcnow() (also naive)
        try:
            stored = entry.locked_at
            # Strip tzinfo if present to normalise to naive
            if stored.tzinfo is not None:
                stored = stored.replace(tzinfo=None)
            lock_age = (datetime.utcnow() - stored).total_seconds()
        except Exception:
            lock_age = 999  # Si falla la comparacion, ignorar el lock
        if lock_age < 300:  # 5 min TTL
            session.close()
            return HTMLResponse(
                f'<div class="text-yellow-400 text-sm">'
                f'Bloqueado por {entry.locked_by}</div>'
            )

    # Adquirir lock (guardar como naive UTC para consistencia con la DB)
    entry.locked_by = "user"
    entry.locked_at = datetime.utcnow()
    session.commit()

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
        <div class="text-sm text-gray-400 mb-1">{entry.original_text[:200]}</div>
        <textarea name="translated_text"
                  class="w-full bg-gray-800 text-white rounded p-2 text-sm min-h-[80px] focus:outline-none focus:ring-2 focus:ring-pink-500"
                  id="editor-ta-{entry_id}">{entry.translated_text or ''}</textarea>
        <div class="flex gap-2 text-xs">
            <button type="submit" class="bg-pink-600 px-3 py-1 rounded hover:bg-pink-700 font-bold">
                💾 Guardar (Ctrl+Enter)
            </button>
            <button type="button"
                    id="cancel-btn-{entry_id}"
                    class="bg-gray-600 px-3 py-1 rounded hover:bg-gray-500"
                    hx-get="/api/texts/{entry_id}/row"
                    hx-target="#entry-{entry_id}"
                    hx-swap="outerHTML">
                ✕ Cancelar
            </button>
        </div>
    </form>
    <script>
        (function() {{
            var ta = document.getElementById('editor-ta-{entry_id}');
            var cancelBtn = document.getElementById('cancel-btn-{entry_id}');
            if (!ta) return;
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
            // Auto-save debounced
            var timer;
            ta.addEventListener('input', function() {{
                clearTimeout(timer);
                timer = setTimeout(() => this.form.requestSubmit(), 2000);
            }});
            ta.focus();
            ta.setSelectionRange(ta.value.length, ta.value.length);
        }})();
    </script>
</div>'''
    session.close()
    return HTMLResponse(html)


@router.put("/{entry_id}")
def update_text(entry_id: int, data: TranslationUpdate):
    """Actualiza la traduccion, hace fit-check, devuelve la fila actualizada."""
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "Texto no encontrado")

    entry.translated_text = data.translated_text
    entry.is_translated = bool(data.translated_text.strip())
    entry.updated_at = datetime.now(timezone.utc)
    entry.locked_by = ""
    entry.locked_at = None

    capacity = entry.segment_capacity or entry.original_bytes or 999
    fit = check_fit(data.translated_text, entry.source, capacity)
    entry.fit_status = fit['status']
    entry.needs_shift = (fit['status'] == 'needs_shift')

    # Actualizar contador del script
    script = entry.script
    if script:
        script.translated_texts = session.query(TextEntry).filter(
            TextEntry.script_id == script.id,
            TextEntry.is_translated == True
        ).count()

    session.commit()

    # Devolver la fila actualizada como HTML
    return _render_entry_row(entry, fit)


def _render_entry_row(entry, fit: dict) -> HTMLResponse:
    """Renderiza una fila de entrada como HTML."""
    fit_icon = {'ok': '🟢', 'tight': '🟡', 'needs_shift': '🔴'}.get(fit['status'], '⚪')
    bg = 'bg-green-900/30' if entry.is_translated else 'bg-gray-800'
    remaining = fit.get('remaining', 0)
    cap_info = f'{remaining} bytes libres' if remaining >= 0 else f'{abs(remaining)} bytes sobre'

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
            {translated_block if translated_block else '<div class="text-xs text-gray-600 italic">Click para traducir</div>'}
        </div>
        <div class="text-xs flex-shrink-0" title="{cap_info}">{fit_icon}</div>
    </div>
</div>'''
    return HTMLResponse(html)


@router.get("/{entry_id}/row", response_class=HTMLResponse)
def get_row(entry_id: int):
    """Devuelve el HTML de la fila (despues de cancelar edicion)."""
    session = get_session()
    entry = session.query(TextEntry).filter(TextEntry.id == entry_id).first()
    if not entry:
        session.close()
        raise HTTPException(404, "No encontrado")

    # Liberar lock
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
                {translated_html if translated_html else '<div class="text-xs text-gray-600 italic">Click para traducir</div>'}
            </div>
            <div class="text-xs">{fit_icon}</div>
        </div>
    </div>'''
    session.close()
    return HTMLResponse(html)

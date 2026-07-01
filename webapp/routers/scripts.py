"""Rutas para listar y ver scripts."""
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader


from ..database import get_session, Script, TextEntry
from ..config import TEMPLATES as TEMPLATES_DIR

router = APIRouter(prefix="/scripts", tags=["scripts"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **kwargs))


@router.get("", response_class=HTMLResponse)
def list_scripts(request: Request, filter_support: str = Query("all")):
    """Lista todos los scripts con barras de progreso."""
    session = get_session()
    query = session.query(Script).order_by(Script.id)

    if filter_support == "supported":
        query = query.filter(Script.is_supported == True)
    elif filter_support == "unsupported":
        query = query.filter(Script.is_supported == False)

    all_scripts = query.all()

    total_texts = sum(s.total_texts or 0 for s in all_scripts)
    total_translated = sum(s.translated_texts or 0 for s in all_scripts)
    pct = round(total_translated / total_texts * 100, 1) if total_texts else 0

    session.close()
    return render("scripts_list.html", request,
        scripts=all_scripts,
        total_texts=total_texts,
        total_translated=total_translated,
        pct=pct,
        filter_support=filter_support,
    )


@router.get("/{script_id}", response_class=HTMLResponse)
def script_detail(
    request: Request,
    script_id: int,
    page: int = Query(1),
    limit: int = Query(50),
    filter: str = Query(None),
    highlight: int = Query(None),
):
    """Vista detalle de un script con sus textos paginados."""
    session = get_session()

    script = session.query(Script).filter(Script.id == script_id).first()
    if not script:
        from fastapi.responses import HTMLResponse as HR
        session.close()
        return HR("<h1>Script no encontrado</h1>", status_code=404)

    # Base query con filtro opcional
    base_q = session.query(TextEntry).filter(TextEntry.script_id == script_id)
    if filter == "untranslated":
        base_q = base_q.filter(TextEntry.is_translated == False)
    elif filter == "translated":
        base_q = base_q.filter(TextEntry.is_translated == True)
    elif filter == "needs_shift":
        base_q = base_q.filter(TextEntry.needs_shift == True)

    total = base_q.count()
    offset = (page - 1) * limit
    texts = base_q.order_by(TextEntry.byte_offset).offset(offset).limit(limit).all()

    # Calcular contexto para cada texto (indices de textos vecinos)
    all_offsets = [
        e.byte_offset for e in session.query(TextEntry.byte_offset).filter(
            TextEntry.script_id == script_id
        ).order_by(TextEntry.byte_offset).all()
    ]

    context_map = {}
    for t in texts:
        idx = all_offsets.index(t.byte_offset) if t.byte_offset in all_offsets else -1
        before = []
        after = []
        if idx > 0:
            before = all_offsets[max(0, idx - 2):idx]
        if idx >= 0 and idx < len(all_offsets) - 1:
            after = all_offsets[idx + 1:min(len(all_offsets), idx + 3)]
        context_map[t.byte_offset] = (before, after)

    # Cargar textos de contexto
    all_context_offsets = set()
    for before, after in context_map.values():
        all_context_offsets.update(before)
        all_context_offsets.update(after)
    context_entries = {}
    if all_context_offsets:
        ctx = session.query(TextEntry).filter(
            TextEntry.script_id == script_id,
            TextEntry.byte_offset.in_(all_context_offsets)
        ).all()
        context_entries = {e.byte_offset: e for e in ctx}

    total_pages = max(1, (total + limit - 1) // limit)

    session.close()
    return render("script_detail.html", request,
        script=script,
        texts=texts,
        context_entries=context_entries,
        context_map=context_map,
        page=page,
        total_pages=total_pages,
        total=total,
        limit=limit,
        highlight=highlight,
    )

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
    section: int = Query(None),
    page: int = Query(1),
    limit: int = Query(50),
    filter: str = Query(None),
    highlight: int = Query(None),
):
    """Vista detalle de un script con secciones y paginacion."""
    session = get_session()

    script = session.query(Script).filter(Script.id == script_id).first()
    if not script:
        session.close()
        return HTMLResponse("<h1>Script no encontrado</h1>", status_code=404)

    # Obtener secciones disponibles
    sections = (
        session.query(TextEntry.section_id)
        .filter(TextEntry.script_id == script_id)
        .group_by(TextEntry.section_id)
        .order_by(TextEntry.section_id)
        .all()
    )
    section_ids = [s[0] for s in sections]
    current_section = section if section is not None else (section_ids[0] if section_ids else 0)

    # Si hay highlight, verificar si esta en la pagina actual; si no, redirigir
    if highlight:
        highlighted = session.query(TextEntry).filter(
            TextEntry.id == highlight,
            TextEntry.script_id == script_id,
        ).first()
        if highlighted:
            pos_in_section = session.query(TextEntry).filter(
                TextEntry.script_id == script_id,
                TextEntry.section_id == highlighted.section_id,
                TextEntry.section_order <= highlighted.section_order,
            ).count()
            target_page = (pos_in_section - 1) // limit + 1
            target_section = highlighted.section_id
            if target_section != current_section or target_page != page:
                session.close()
                from fastapi.responses import RedirectResponse
                url = f"/scripts/{script_id}?section={target_section}&page={target_page}&highlight={highlight}"
                if filter:
                    url += f"&filter={filter}"
                return RedirectResponse(url=url)

    # Base query filtrada por seccion
    base_q = session.query(TextEntry).filter(
        TextEntry.script_id == script_id,
        TextEntry.section_id == current_section,
    )
    if filter == "untranslated":
        base_q = base_q.filter(TextEntry.is_translated == False)
    elif filter == "translated":
        base_q = base_q.filter(TextEntry.is_translated == True)
    elif filter == "needs_shift":
        base_q = base_q.filter(TextEntry.needs_shift == True)

    total = base_q.count()
    offset = (page - 1) * limit
    texts = base_q.order_by(TextEntry.section_order).offset(offset).limit(limit).all()

    # Calcular contexto (textos vecinos por section_order)
    all_orders = [
        (e.byte_offset, e.section_order) for e in session.query(
            TextEntry.byte_offset, TextEntry.section_order
        ).filter(
            TextEntry.script_id == script_id,
            TextEntry.section_id == current_section,
        ).order_by(TextEntry.section_order).all()
    ]

    order_to_offset = {o: off for off, o in all_orders}
    offset_to_order = {off: o for off, o in all_orders}

    context_map = {}
    for t in texts:
        o = offset_to_order.get(t.byte_offset, 0)
        before = [order_to_offset.get(o - 2), order_to_offset.get(o - 1)]
        after = [order_to_offset.get(o + 1), order_to_offset.get(o + 2)]
        before = [b for b in before if b is not None]
        after = [a for a in after if a is not None]
        context_map[t.byte_offset] = (before, after)

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

    # Stats por seccion
    section_stats = {}
    for sec_id in section_ids:
        sec_total = session.query(TextEntry).filter(
            TextEntry.script_id == script_id,
            TextEntry.section_id == sec_id,
        ).count()
        sec_trans = session.query(TextEntry).filter(
            TextEntry.script_id == script_id,
            TextEntry.section_id == sec_id,
            TextEntry.is_translated == True,
        ).count()
        section_stats[sec_id] = (sec_total, sec_trans)

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
        section_ids=section_ids,
        current_section=current_section,
        section_stats=section_stats,
        filter=filter,
    )

"""Rutas de busqueda, dashboard y delegacion."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text, func

from ..config import TEMPLATES as TEMPLATES_DIR, BUILD_TEMP_DIR, TEXTOS
from ..database import get_session, TextEntry, Script

router = APIRouter(tags=["tools"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)

# Add urlencode filter
from urllib.parse import quote_plus
env.filters['urlencode'] = lambda s: quote_plus(str(s))


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **kwargs))


# ============================================================
# DASHBOARD
# ============================================================

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    """Dashboard con stats globales del proyecto."""
    session = get_session()

    total_scripts = session.query(func.count(Script.id)).scalar() or 0
    total_texts = session.query(func.count(TextEntry.id)).scalar() or 0
    translated = session.query(func.count(TextEntry.id)).filter(
        TextEntry.is_translated == True
    ).scalar() or 0
    needs_shift = session.query(func.count(TextEntry.id)).filter(
        TextEntry.needs_shift == True
    ).scalar() or 0
    pct = round(translated / total_texts * 100, 1) if total_texts else 0

    # Top scripts by progress
    top_scripts = session.query(Script).filter(
        Script.total_texts > 0
    ).order_by(
        (Script.translated_texts * 100 / Script.total_texts).desc()
    ).limit(10).all()

    # Scripts with 100% completion
    completed = session.query(func.count(Script.id)).filter(
        Script.total_texts > 0,
        Script.translated_texts >= Script.total_texts
    ).scalar() or 0

    # Recent activity (last edited texts)
    recent = session.query(TextEntry).filter(
        TextEntry.is_translated == True,
        TextEntry.updated_at.isnot(None)
    ).order_by(TextEntry.updated_at.desc()).limit(10).all()

    session.close()
    return render("dashboard.html", request,
        total_scripts=total_scripts, total_texts=total_texts,
        translated=translated, needs_shift=needs_shift, pct=pct,
        completed=completed, top_scripts=top_scripts, recent=recent)


# ============================================================
# SEARCH (FTS5)
# ============================================================

@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = Query(""), search_type: str = Query("both")):
    return render("search.html", request, results=None, query=q, search_type=search_type)


@router.get("/api/search")
def search_texts(request: Request, q: str = Query(""), script_id: int = Query(None),
                 search_type: str = Query("both"), page: int = Query(1), limit: int = Query(50)):
    """Busqueda con LIKE (compatible con CJK)."""
    if not q.strip():
        return render("components/search_results.html", request,
                       results=[], total=0, query=q, error=None)

    session = get_session()
    offset = (page - 1) * limit
    like_q = f"%{q}%"

    try:
        if search_type == "jp":
            base = session.query(TextEntry).filter(TextEntry.original_text.like(like_q))
        elif search_type == "es":
            base = session.query(TextEntry).filter(TextEntry.translated_text.like(like_q))
        else:
            base = session.query(TextEntry).filter(
                (TextEntry.original_text.like(like_q)) |
                (TextEntry.translated_text.like(like_q))
            )

        if script_id:
            base = base.filter(TextEntry.script_id == script_id)

        total = base.count()
        rows = base.order_by(TextEntry.script_id, TextEntry.byte_offset).offset(offset).limit(limit).all()

    except Exception as e:
        session.close()
        return render("components/search_results.html", request,
                       results=[], total=0, query=q, error=str(e)[:100])

    results = []
    for entry in rows:
        results.append({
            "id": entry.id, "script_id": entry.script_id,
            "byte_offset": entry.byte_offset,
            "section_id": entry.section_id,
            "section_order": entry.section_order,
            "original_text": entry.original_text[:200],
            "translated_text": entry.translated_text[:200] if entry.translated_text else "",
            "is_translated": entry.is_translated,
            "needs_shift": entry.needs_shift,
            "fit_status": entry.fit_status or "unchecked",
        })

    # Si es busqueda dentro de un script, calcular seccion y pagina
    page_size = 50
    if script_id is not None:
        # Obtener posicion por section_order dentro de cada seccion
        section_orders = (
            session.query(TextEntry.section_id, TextEntry.section_order, TextEntry.byte_offset)
            .filter(TextEntry.script_id == script_id)
            .order_by(TextEntry.section_id, TextEntry.section_order)
            .all()
        )
        # Mapa: byte_offset -> (section_id, position_in_section)
        sec_positions = {}
        sec_counters = {}
        for sec_id, sec_order, boff in section_orders:
            idx = sec_counters.get(sec_id, 0)
            sec_positions[boff] = (sec_id, idx)
            sec_counters[sec_id] = idx + 1

        for r in results:
            sec_id, pos = sec_positions.get(r['byte_offset'], (r['section_id'], 0))
            r['section_id'] = sec_id
            r['page_num'] = pos // page_size + 1
    else:
        for r in results:
            r['page_num'] = 1

    total_pages = max(1, (total + limit - 1) // limit)
    session.close()
    return render("components/search_results.html", request,
                   results=results, total=total, query=q,
                   search_type=search_type, page=page, total_pages=total_pages,
                   script_id=script_id, error=None)


# ============================================================
# DELEGATION
# ============================================================

@router.get("/delegation", response_class=HTMLResponse)
def delegation_page(request: Request):
    """Pagina de delegacion de traducciones."""
    session = get_session()
    scripts = session.query(Script).filter(Script.total_texts > 0).order_by(Script.id).all()
    session.close()
    return render("delegation.html", request, scripts=scripts)


@router.post("/api/delegation/export")
def export_partial(request: Request):
    """Exporta un rango de scripts como CSV parcial para delegar."""
    body = request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Try form data
        form = request.form()
        # ... handled below

    script_ids = data.get("script_ids", [])
    label = data.get("label", "parcial")
    only_untranslated = data.get("only_untranslated", False)

    if not script_ids:
        raise HTTPException(400, "Especifica al menos un script_id")

    session = get_session()
    query = session.query(TextEntry).filter(TextEntry.script_id.in_(script_ids))
    if only_untranslated:
        query = query.filter(TextEntry.is_translated == False)
    entries = query.order_by(TextEntry.script_id, TextEntry.section_id, TextEntry.section_order).all()
    session.close()

    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"parcial_{label.replace(' ', '_')}.csv"
    csv_path = BUILD_TEMP_DIR / filename

    import csv as csv_mod
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv_mod.writer(f, lineterminator='\r\n')
        writer.writerow(['source', 'file_id', 'offset', 'section', 'section_order',
                         'original_text', 'translated_text'])
        for entry in entries:
            source = entry.source
            fid = str(entry.script_id) if entry.script_id != -1 else 'ELF'
            off = f"0x{entry.byte_offset:05X}" if source == 'SCRIPT' else f"0x{entry.byte_offset:06X}"
            writer.writerow([source, fid, off, entry.section_id, entry.section_order,
                             entry.original_text, entry.translated_text or ''])

    return FileResponse(
        csv_path, media_type="text/csv",
        filename=filename,
        headers={"X-Total-Count": str(len(entries))}
    )


@router.post("/api/delegation/import")
async def import_partial(request: Request):
    """Importa un CSV parcial con traducciones. Hace merge a la DB."""
    from fastapi import UploadFile, Form

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file:
        raise HTTPException(400, "No se subio ningun archivo")

    content = await file.read()
    import io
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))

    session = get_session()
    stats = {"imported": 0, "conflicts": 0, "skipped": 0, "total": 0}

    for row in reader:
        stats["total"] += 1
        translated = row.get('translated_text', '').strip()
        if not translated:
            stats["skipped"] += 1
            continue

        source = row.get('source', 'SCRIPT')
        file_id = row.get('file_id', '0')
        try:
            byte_offset = int(row.get('offset', '0x0'), 16)
        except ValueError:
            stats["skipped"] += 1
            continue
        original = row.get('original_text', '')

        sid = -1 if file_id == 'ELF' else int(file_id)

        entry = session.query(TextEntry).filter(
            TextEntry.script_id == sid,
            TextEntry.byte_offset == byte_offset,
            TextEntry.original_text == original
        ).first()

        if entry:
            if entry.translated_text and entry.translated_text != translated:
                stats["conflicts"] += 1
            elif not entry.translated_text or entry.translated_text != translated:
                entry.translated_text = translated
                entry.is_translated = True
                entry.updated_at = datetime.now(timezone.utc)
                stats["imported"] += 1
            else:
                stats["skipped"] += 1
        else:
            stats["skipped"] += 1

    session.commit()
    session.close()

    return JSONResponse({"status": "ok", "stats": stats})


# ============================================================
# BATCH OPERATIONS
# ============================================================

@router.post("/api/batch/unlock")
def batch_unlock():
    """Desbloquea todos los textos (admin)."""
    session = get_session()
    count = session.query(TextEntry).filter(TextEntry.locked_by != '').update(
        {"locked_by": "", "locked_at": None}
    )
    session.commit()
    session.close()
    return JSONResponse({"unlocked": count})

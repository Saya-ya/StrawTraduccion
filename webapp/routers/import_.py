from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR, TEXTOS
from ..services.import_service import import_csv_to_db
from ..services.build_lock import acquire_build_lock, is_build_running, release_build_lock

router = APIRouter(prefix="/import", tags=["import"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    ctx = getattr(request.state, "i18n", {})
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **ctx, **kwargs))


@router.get("", response_class=HTMLResponse)
def import_page(request: Request):
    return render("import.html", request)


@router.post("/run")
def run_import():
    if not acquire_build_lock():
        return JSONResponse(
            {"status": "error", "message": "Ya hay una operacion en progreso"},
            status_code=409
        )

    try:
        stats = import_csv_to_db(TEXTOS / 'dialogo.csv')
        return JSONResponse({"status": "ok", "stats": stats})
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )
    finally:
        release_build_lock()

"""Rutas de build y exportacion CSV."""
import json
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR, BUILD_TEMP_DIR, TEXTOS
from ..services.builder import export_csv_for_build, run_full_build
from ..services.build_lock import get_build_state, is_build_running

router = APIRouter(prefix="/build", tags=["build"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **kwargs))


@router.get("", response_class=HTMLResponse)
def build_page(request: Request):
    """Pagina de build con boton y log en vivo."""
    state = get_build_state()
    running = is_build_running()
    return render("build.html", request, state=state, running=running)


@router.get("/status")
def build_status():
    """Devuelve el estado actual del build (polling)."""
    state = get_build_state()
    running = is_build_running()
    state["running"] = running
    return JSONResponse(state)


@router.post("/run")
def trigger_build():
    """Dispara el pipeline de build en un proceso independiente."""
    if is_build_running():
        return JSONResponse(
            {"status": "error", "message": "Build ya en progreso"},
            status_code=409
        )

    build_id = uuid.uuid4().hex[:8]
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Lanzar build en proceso hijo independiente
    worker_script = Path(__file__).parent.parent.parent / "build_worker.py"
    subprocess.Popen(
        ["python3", str(worker_script), build_id],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent.parent.parent)
    )

    return JSONResponse({"status": "started", "build_id": build_id})


@router.get("/export/csv")
def download_csv():
    """Descarga el CSV completo con traducciones."""
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = BUILD_TEMP_DIR / "dialogo_export.csv"
    count = export_csv_for_build(csv_path, only_translated=False)
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"dialogo_{count}_textos.csv"
    )


@router.get("/export/csv/translated")
def download_csv_translated():
    """Descarga solo los textos traducidos."""
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = BUILD_TEMP_DIR / "dialogo_traducidos.csv"
    count = export_csv_for_build(csv_path, only_translated=True)
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"dialogo_traducidos_{count}.csv"
    )

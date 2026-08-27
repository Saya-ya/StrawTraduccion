import json
import os
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR, BUILD_TEMP_DIR, TEXTOS
from ..services.builder import DEFAULT_COMPRESS_WORKERS, export_csv_for_build, run_full_build
from ..services.build_lock import get_build_state, is_build_running

router = APIRouter(prefix="/build", tags=["build"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    ctx = getattr(request.state, "i18n", {})
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **ctx, **kwargs))


@router.get("", response_class=HTMLResponse)
def build_page(request: Request):
    state = get_build_state()
    running = is_build_running()
    max_workers = max(1, min(os.cpu_count() or 2, 8))
    return render(
        "build.html",
        request,
        state=state,
        running=running,
        default_workers=DEFAULT_COMPRESS_WORKERS,
        max_workers=max_workers,
    )


@router.get("/status")
def build_status():
    state = get_build_state()
    running = is_build_running()
    state["running"] = running
    return JSONResponse(state)


@router.post("/run")
async def trigger_build(request: Request):
    if is_build_running():
        return JSONResponse(
            {"status": "error", "message": "Build ya en progreso"},
            status_code=409
        )

    build_id = uuid.uuid4().hex[:8]
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    build_type = "full"
    workers = DEFAULT_COMPRESS_WORKERS
    try:
        payload = await request.json()
        build_type = payload.get("build_type", build_type)
        workers = int(payload.get("workers", workers))
    except Exception:
        pass

    if build_type not in {"full", "texts", "images"}:
        return JSONResponse(
            {"status": "error", "message": "Tipo de build invalido"},
            status_code=400,
        )
    workers = max(1, min(workers, max(1, min(os.cpu_count() or 2, 8))))

    worker_script = Path(__file__).parent.parent.parent / "build_worker.py"
    venv_python = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else "python3"
    subprocess.Popen(
        [python_exe, str(worker_script), build_id, build_type, str(workers)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent.parent.parent)
    )

    return JSONResponse({"status": "started", "build_id": build_id, "build_type": build_type, "workers": workers})


@router.get("/export/csv")
def download_csv():
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
    BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = BUILD_TEMP_DIR / "dialogo_traducidos.csv"
    count = export_csv_for_build(csv_path, only_translated=True)
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"dialogo_traducidos_{count}.csv"
    )

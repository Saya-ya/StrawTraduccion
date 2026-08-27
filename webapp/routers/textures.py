import json
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR, PROJECT_ROOT, TEXTURE_CATALOG
from ..services.build_lock import acquire_build_lock, is_build_running, release_build_lock

router = APIRouter(prefix="/textures", tags=["textures"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)

CATALOG_DIR = TEXTURE_CATALOG
MANIFEST_DIR = PROJECT_ROOT / "texturas"
MANIFEST_PATH = MANIFEST_DIR / "manifest.json"
EXTRACT_SCRIPT = PROJECT_ROOT / "tools" / "extract_all_textures.py"
CATALOG_SCRIPT = PROJECT_ROOT / "tools" / "texture_catalog.py"
UPLOAD_DIR = MANIFEST_DIR

PNG_PATTERN = re.compile(
    r"^ID_(\d{5})(?:_L([0-9A-Fa-f]{6}))?_T(\d{3})_P(\d{2})_\d+x\d+\.png$"
)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    ctx = getattr(request.state, "i18n", {})
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **ctx, **kwargs))


@router.get("", response_class=HTMLResponse)
def textures_page(request: Request):
    textures_json = CATALOG_DIR / "textures.json"
    stats = {}
    if textures_json.exists():
        try:
            data = json.loads(textures_json.read_text())
            stats["total"] = len(data)
            stats["with_png"] = sum(1 for r in data if r.get("png"))
        except Exception:
            stats["total"] = 0
            stats["with_png"] = 0
    else:
        stats["total"] = 0
        stats["with_png"] = 0

    manifest = []
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except Exception:
            pass

    running = is_build_running()

    png_files = []
    if UPLOAD_DIR.exists():
        for f in sorted(UPLOAD_DIR.iterdir()):
            if f.suffix.lower() == ".png":
                m = PNG_PATTERN.match(f.name)
                entry = {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "valid": m is not None,
                    "file_id": int(m.group(1)) if m else None,
                    "lz77_offset": int(m.group(2), 16) if m and m.group(2) else None,
                    "tim2_idx": int(m.group(3)) if m else None,
                    "pic_idx": int(m.group(4)) if m else None,
                    "catalog_size": None,
                }
                if m:
                    cat_png = CATALOG_DIR / "png" / f.name
                    if cat_png.exists():
                        entry["catalog_size"] = cat_png.stat().st_size
                png_files.append(entry)

    return render(
        "textures.html",
        request,
        stats=stats,
        manifest=manifest,
        png_files=png_files,
        running=running,
        manifest_exists=MANIFEST_PATH.exists(),
    )


@router.post("/extract")
def extract_textures():
    if not acquire_build_lock():
        return JSONResponse(
            {"status": "error", "message": "Operacion en progreso"},
            status_code=409,
        )

    try:
        CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["python3", str(EXTRACT_SCRIPT), "--out", str(CATALOG_DIR)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
            timeout=600,
        )

        if result.returncode != 0:
            return JSONResponse({
                "status": "error",
                "message": result.stderr[-500:] or result.stdout[-500:],
            })

        catalog_html = CATALOG_DIR / "index.html"
        if not catalog_html.exists():
            try:
                subprocess.run(
                    ["python3", str(CATALOG_SCRIPT), "--dir", str(CATALOG_DIR)],
                    capture_output=True, text=True,
                    cwd=str(PROJECT_ROOT),
                    timeout=120,
                )
            except Exception:
                pass

        textures_json = CATALOG_DIR / "textures.json"
        stats = {"total": 0, "with_png": 0}
        if textures_json.exists():
            data = json.loads(textures_json.read_text())
            stats["total"] = len(data)
            stats["with_png"] = sum(1 for r in data if r.get("png"))

        return JSONResponse({
            "status": "ok",
            "stats": stats,
            "stdout": result.stdout[-1000:],
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({
            "status": "error",
            "message": "Timeout tras 600s",
        })
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "message": str(e),
        })
    finally:
        release_build_lock()


@router.post("/upload")
async def upload_textures(files: list[UploadFile] = File(...)):
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = []
    errors = []

    for f in files:
        if not f.filename or not f.filename.lower().endswith(".png"):
            errors.append(f"{f.filename}: no es PNG")
            continue

        m = PNG_PATTERN.match(f.filename)
        if not m:
            errors.append(f"{f.filename}: nombre no reconocido")
            continue

        dest = MANIFEST_DIR / f.filename
        content = await f.read()
        original_size = len(content)

        dest.write_bytes(content)

        try:
            sys.path.insert(0, str(PROJECT_ROOT / "dev"))
            sys.path.insert(0, str(PROJECT_ROOT / "tools"))
            from tim2_encode import read_png_rgba
            from tim2_png import write_png_rgba

            w, h, rgba = read_png_rgba(dest)
            write_png_rgba(dest, w, h, rgba)
            recompressed_size = dest.stat().st_size
        except Exception:
            recompressed_size = original_size

        uploaded.append({
            "name": f.filename,
            "original_size": original_size,
            "recompressed_size": recompressed_size,
        })

    return JSONResponse({
        "status": "ok",
        "uploaded": uploaded,
        "errors": errors,
    })


@router.post("/manifest/generate")
def generate_manifest():
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

    patches = []
    for f in sorted(MANIFEST_DIR.iterdir()):
        if not f.suffix.lower() == ".png":
            continue
        m = PNG_PATTERN.match(f.name)
        if not m:
            continue

        patch = {
            "file_id": int(m.group(1)),
            "tim2_index": int(m.group(3)),
            "picture_index": int(m.group(4)),
            "png": f"texturas/{f.name}",
            "mode": "preserve_palette",
        }
        if m.group(2):
            patch["lz77_offset"] = int(m.group(2), 16)
        patches.append(patch)

    MANIFEST_PATH.write_text(
        json.dumps(patches, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return JSONResponse({
        "status": "ok",
        "count": len(patches),
        "manifest": patches,
    })


@router.get("/manifest")
def get_manifest():
    if not MANIFEST_PATH.exists():
        return JSONResponse([])
    return JSONResponse(json.loads(MANIFEST_PATH.read_text()))


@router.post("/catalog/build")
def build_catalog():
    if not (CATALOG_DIR / "textures.json").exists():
        return JSONResponse({"status": "error", "message": "Extrae texturas primero"})

    try:
        result = subprocess.run(
            ["python3", str(CATALOG_SCRIPT), "--dir", str(CATALOG_DIR)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
            timeout=120,
        )
        return JSONResponse({
            "status": "ok" if result.returncode == 0 else "error",
            "stdout": result.stdout[-500:],
            "stderr": result.stderr[-500:],
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})

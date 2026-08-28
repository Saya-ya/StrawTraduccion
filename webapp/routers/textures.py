import hashlib
import csv
import contextlib
import io
import json
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from ..config import TEMPLATES as TEMPLATES_DIR, PROJECT_ROOT, TEXTURE_CATALOG, WORK_TEXTURES, ORIGINALES
from ..services.build_lock import acquire_build_lock, is_build_running, release_build_lock

router = APIRouter(prefix="/textures", tags=["textures"])
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), auto_reload=False)

CATALOG_DIR = TEXTURE_CATALOG
MANIFEST_DIR = PROJECT_ROOT / "texturas"
MANIFEST_PATH = MANIFEST_DIR / "manifest.json"
CATALOG_SCRIPT = PROJECT_ROOT / "tools" / "texture_catalog.py"
UPLOAD_DIR = MANIFEST_DIR
EXTRACT_STATE_FILE = WORK_TEXTURES / "texture_extract_state.json"
DUPLICATE_STATE_FILE = WORK_TEXTURES / "texture_duplicate_state.json"
DUPLICATES_PATH = CATALOG_DIR / "duplicates.json"
DUPLICATES_CSV_PATH = CATALOG_DIR / "duplicates.csv"

PNG_PATTERN = re.compile(
    r"^ID_(\d{5})(?:_L([0-9A-Fa-f]{6}))?_T(\d{3})_P(\d{2})_\d+x\d+\.png$"
)


def render(name: str, request: Request, **kwargs) -> HTMLResponse:
    ctx = getattr(request.state, "i18n", {})
    template = env.get_template(name)
    return HTMLResponse(template.render(request=request, **ctx, **kwargs))


def _write_extract_state(state: dict) -> None:
    EXTRACT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTRACT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_extract_state() -> dict:
    if not EXTRACT_STATE_FILE.exists():
        return {"status": "idle", "progress": 0}
    try:
        return json.loads(EXTRACT_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "idle", "progress": 0}


def _write_duplicate_state(state: dict) -> None:
    DUPLICATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DUPLICATE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _read_duplicate_state() -> dict:
    if not DUPLICATE_STATE_FILE.exists():
        return {"status": "idle", "progress": 0}
    try:
        return json.loads(DUPLICATE_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "idle", "progress": 0}


def _texture_stats() -> dict:
    textures_json = CATALOG_DIR / "textures.json"
    stats = {"total": 0, "with_png": 0}
    if textures_json.exists():
        try:
            data = json.loads(textures_json.read_text(encoding="utf-8"))
            stats["total"] = len(data)
            stats["with_png"] = sum(1 for r in data if r.get("png"))
        except Exception:
            pass
    return stats


def _read_duplicates() -> dict:
    empty = {"groups": [], "by_file": {}, "summary": {"groups": 0, "duplicate_files": 0}}
    if not DUPLICATES_PATH.exists():
        return empty
    try:
        return json.loads(DUPLICATES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty


def _catalog_png_name(record: dict) -> str | None:
    png = record.get("png")
    if not png:
        return None
    return Path(png).name


def _rgba_hash(png_path: Path) -> tuple[str, int, int] | None:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from tim2_encode import read_png_rgba

    try:
        width, height, rgba = read_png_rgba(png_path)
    except Exception:
        return None

    h = hashlib.sha256()
    h.update(f"{width}x{height}".encode("ascii"))
    h.update(b"\0")
    h.update(rgba)
    return h.hexdigest(), width, height


def _analyze_duplicates(progress_callback=None) -> dict:
    textures_json = CATALOG_DIR / "textures.json"
    if not textures_json.exists():
        raise FileNotFoundError("Primero extrae texturas para generar textures.json")

    records = json.loads(textures_json.read_text(encoding="utf-8"))
    by_hash: dict[str, list[dict]] = {}

    total = len(records)
    for processed, record in enumerate(records, start=1):
        png_name = _catalog_png_name(record)
        if not png_name:
            if progress_callback and processed % 25 == 0:
                progress_callback(processed, total, 0)
            continue
        png_path = CATALOG_DIR / "png" / png_name
        if not png_path.exists():
            if progress_callback and processed % 25 == 0:
                progress_callback(processed, total, 0)
            continue
        digest = record.get("content_hash")
        width = record.get("width")
        height = record.get("height")
        if not digest:
            hashed = _rgba_hash(png_path)
            if hashed is None:
                continue
            digest, width, height = hashed
        item = {
            "name": png_name,
            "id": record.get("id"),
            "tim2_index": record.get("tim2_index"),
            "picture_index": record.get("picture_index"),
            "nested_lz77_offset": record.get("nested_lz77_offset"),
            "width": width,
            "height": height,
            "png": record.get("png"),
        }
        by_hash.setdefault(digest, []).append(item)
        if progress_callback and (processed % 25 == 0 or processed == total):
            progress_callback(processed, total, sum(1 for v in by_hash.values() if len(v) > 1))

    groups = []
    by_file = {}
    duplicate_hashes = sorted((k, v) for k, v in by_hash.items() if len(v) > 1)
    for index, (digest, files) in enumerate(duplicate_hashes, start=1):
        group_id = f"dup_{index:04d}"
        names = [f["name"] for f in files]
        group = {
            "group_id": group_id,
            "hash": digest,
            "count": len(files),
            "width": files[0]["width"],
            "height": files[0]["height"],
            "files": files,
        }
        groups.append(group)
        for name in names:
            by_file[name] = {
                "group_id": group_id,
                "count": len(names),
                "duplicates": [n for n in names if n != name],
            }

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "groups": len(groups),
            "duplicate_files": sum(g["count"] for g in groups),
        },
        "groups": groups,
        "by_file": by_file,
    }
    DUPLICATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DUPLICATES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    with DUPLICATES_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group_id", "hash", "name", "duplicate_count", "duplicates"])
        for group in groups:
            names = [item["name"] for item in group["files"]]
            for name in names:
                writer.writerow([
                    group["group_id"],
                    group["hash"],
                    name,
                    len(names) - 1,
                    ";".join(n for n in names if n != name),
                ])
    return data


def _run_duplicate_analysis_job() -> None:
    started = time.time()
    try:
        def progress(processed: int, total: int, groups: int) -> None:
            pct = int((processed / total) * 95) if total else 0
            _write_duplicate_state({
                "status": "running",
                "progress": pct,
                "processed": processed,
                "total_files": total,
                "groups": groups,
                "elapsed": round(time.time() - started, 1),
                "message": f"Analizados {processed}/{total} PNGs; {groups} grupos candidatos",
            })

        data = _analyze_duplicates(progress_callback=progress)
        summary = data["summary"]
        _write_duplicate_state({
            "status": "success",
            "progress": 100,
            "summary": summary,
            "elapsed": round(time.time() - started, 1),
            "message": f"Completado: {summary['groups']} grupos duplicados; {summary['duplicate_files']} archivos relacionados",
        })
    except Exception as e:
        _write_duplicate_state({
            "status": "error",
            "progress": 100,
            "message": str(e),
            "elapsed": round(time.time() - started, 1),
        })
    finally:
        release_build_lock()


def _run_texture_extraction_job() -> None:
    started = time.time()
    try:
        data_bin = ORIGINALES / "Data.bin"
        if not data_bin.exists():
            raise FileNotFoundError(f"No existe {data_bin}")

        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from extract_all_textures import extract_all, write_outputs
        from texture_catalog import build_catalog

        CATALOG_DIR.mkdir(parents=True, exist_ok=True)

        def progress(processed: int, total: int, records: int, elapsed: float) -> None:
            pct = int((processed / total) * 95) if total else 0
            _write_extract_state({
                "status": "running",
                "progress": pct,
                "processed": processed,
                "total_files": total,
                "textures_found": records,
                "elapsed": round(elapsed, 1),
                "message": f"Procesados {processed}/{total} archivos; {records} texturas encontradas",
            })

        records = extract_all(data_bin, CATALOG_DIR, progress_every=0, progress_callback=progress)
        _write_extract_state({
            "status": "running",
            "progress": 97,
            "textures_found": len(records),
            "message": "Escribiendo metadatos y catálogo HTML...",
        })
        write_outputs(CATALOG_DIR, records)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            build_catalog(CATALOG_DIR)

        stats = _texture_stats()
        _write_extract_state({
            "status": "success",
            "progress": 100,
            "stats": stats,
            "elapsed": round(time.time() - started, 1),
            "message": f"Completado: {stats['total']} texturas catalogadas ({stats['with_png']} PNGs)",
        })
    except Exception as e:
        _write_extract_state({
            "status": "error",
            "progress": 100,
            "message": str(e),
            "elapsed": round(time.time() - started, 1),
        })
    finally:
        release_build_lock()


@router.get("", response_class=HTMLResponse)
def textures_page(request: Request):
    stats = _texture_stats()
    duplicates = _read_duplicates()
    duplicate_map = duplicates.get("by_file", {})

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
                    dup = duplicate_map.get(f.name)
                    entry["duplicate_count"] = len(dup.get("duplicates", [])) if dup else 0
                    entry["duplicate_group"] = dup.get("group_id", "") if dup else ""
                png_files.append(entry)

    return render(
        "textures.html",
        request,
        stats=stats,
        manifest=manifest,
        png_files=png_files,
        running=running,
        manifest_exists=MANIFEST_PATH.exists(),
        extract_state=_read_extract_state(),
        duplicate_state=_read_duplicate_state(),
        duplicates=duplicates,
    )


@router.post("/extract")
def extract_textures():
    if not acquire_build_lock():
        return JSONResponse(
            {"status": "error", "message": "Operacion en progreso"},
            status_code=409,
        )

    _write_extract_state({"status": "running", "progress": 0, "message": "Iniciando extracción..."})
    thread = threading.Thread(target=_run_texture_extraction_job, daemon=True)
    thread.start()
    return JSONResponse({"status": "started"})


@router.get("/extract/status")
def texture_extract_status():
    state = _read_extract_state()
    running = state.get("status") == "running" and is_build_running()
    if state.get("status") == "running" and not running:
        state["status"] = "error"
        state["message"] = "La extracción se detuvo antes de terminar. Intenta ejecutar nuevamente."
    state["running"] = running
    return JSONResponse(state)


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


@router.post("/duplicates/analyze", response_class=HTMLResponse)
def analyze_duplicates():
    if not acquire_build_lock():
        return JSONResponse(
            {"status": "error", "message": "Hay una operación en progreso"},
            status_code=409,
        )

    _write_duplicate_state({"status": "running", "progress": 0, "message": "Iniciando análisis de duplicados..."})
    thread = threading.Thread(target=_run_duplicate_analysis_job, daemon=True)
    thread.start()
    return JSONResponse({"status": "started"})


@router.get("/duplicates/status")
def duplicate_analysis_status():
    state = _read_duplicate_state()
    running = state.get("status") == "running" and is_build_running()
    if state.get("status") == "running" and not running:
        state["status"] = "error"
        state["message"] = "El análisis se detuvo antes de terminar. Intenta ejecutar nuevamente."
    state["running"] = running
    return JSONResponse(state)


@router.post("/duplicates/apply/{filename:path}", response_class=HTMLResponse)
def apply_duplicate_group(filename: str):
    if "/" in filename or "\\" in filename:
        return HTMLResponse('<div class="text-red-400">Nombre inválido</div>', status_code=400)

    duplicates = _read_duplicates()
    info = duplicates.get("by_file", {}).get(filename)
    if not info:
        return HTMLResponse('<div class="text-yellow-400">Esta textura no tiene duplicados registrados.</div>')

    src = UPLOAD_DIR / filename
    if not src.exists():
        return HTMLResponse('<div class="text-red-400">Primero sube o guarda esta textura en texturas/.</div>')

    copied = []
    for target_name in info.get("duplicates", []):
        if "/" in target_name or "\\" in target_name:
            continue
        dst = UPLOAD_DIR / target_name
        shutil.copy2(src, dst)
        copied.append(target_name)

    if not copied:
        return HTMLResponse('<div class="text-yellow-400">No había duplicados aplicables.</div>')

    return HTMLResponse(
        f'<div class="text-green-400">✓ Copiada a {len(copied)} duplicados. '
        f'Ahora genera el manifiesto para incluirlos en el build.</div>'
    )


@router.post("/catalog/build")
def build_catalog():
    if not (CATALOG_DIR / "textures.json").exists():
        return JSONResponse({"status": "error", "message": "Extrae texturas primero"})

    try:
        import subprocess
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

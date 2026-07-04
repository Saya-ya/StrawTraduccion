"""Build pipeline — orquesta patch_dec.py + build_iso.py + inject_elf.py."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

from ..config import (
    TIMEOUT_REBUILD, TIMEOUT_BUILD_ISO, TIMEOUT_INJECT_ELF,
    BUILD_TEMP_DIR, BUILD_STATE_FILE, WORK
)
from ..database import get_session, TextEntry, Script, BuildHistory
from .build_lock import acquire_build_lock, release_build_lock, write_build_state


def export_csv_for_build(csv_path: Path, only_translated: bool = True) -> int:
    """Exporta textos traducidos a CSV en formato CLI. Retorna conteo."""
    import csv
    session = get_session()

    query = session.query(TextEntry).order_by(TextEntry.script_id, TextEntry.section_id, TextEntry.section_order)
    if only_translated:
        query = query.filter(TextEntry.is_translated == True)

    entries = query.all()
    count = 0

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f, lineterminator='\r\n')
        writer.writerow(['source', 'file_id', 'offset', 'original_text', 'translated_text'])

        for entry in entries:
            source = entry.source
            file_id = str(entry.script_id) if entry.script_id != -1 else 'ELF'
            offset = f"0x{entry.byte_offset:05X}" if source == 'SCRIPT' else f"0x{entry.byte_offset:06X}"
            writer.writerow([source, file_id, offset, entry.original_text, entry.translated_text or ''])
            count += 1

    session.close()
    return count


def run_rebuild(script_id: int, csv_path: Path) -> dict:
    """Ejecuta patch_dec.py --rebuild para un script."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "tools" / "patch_dec.py"),
         "--id", str(script_id), "--rebuild", "--csv", str(csv_path), "--verify"],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_REBUILD
    )
    return {
        "success": result.returncode == 0,
        "script_id": script_id,
        "stdout": result.stdout[-300:],
        "stderr": result.stderr[-300:],
    }


def run_apply_translation(csv_path: Path) -> dict:
    """Ejecuta apply_translation.py para generar SLPS_256.11_translated."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "traduccion_tools" / "apply_translation.py"),
         str(csv_path)],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_BUILD_ISO
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout[-300:],
        "stderr": result.stderr[-300:],
    }


def run_build_iso() -> dict:
    """Ejecuta build_iso.py."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "traduccion_tools" / "build_iso.py")],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_BUILD_ISO
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout[-300:],
        "stderr": result.stderr[-300:],
    }


def run_inject_elf() -> dict:
    """Ejecuta inject_elf.py."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "traduccion_tools" / "inject_elf.py")],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
        timeout=TIMEOUT_INJECT_ELF
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout[-300:],
        "stderr": result.stderr[-300:],
    }


def run_full_build(build_id: str):
    """Pipeline completo: export → rebuild scripts → ISO → ELF.

    Escribe progreso a BUILD_STATE_FILE en cada paso.
    """
    state = {"id": build_id, "status": "running", "step": "", "progress": 0, "log": []}

    def save():
        BUILD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BUILD_STATE_FILE.write_text(json.dumps(state))

    if not acquire_build_lock():
        state["status"] = "failed"
        state["error"] = "Build lock ocupado — otro build en progreso"
        save()
        return

    try:
        session = get_session()

        # 1. Obtener scripts con traducciones
        translated_scripts = session.query(TextEntry.script_id).filter(
            TextEntry.is_translated == True,
            TextEntry.script_id != -1  # skip ELF
        ).distinct().all()
        script_ids = sorted([s[0] for s in translated_scripts])

        if not script_ids:
            state["status"] = "failed"
            state["error"] = "No hay traducciones para build"
            save()
            return

        # 2. Exportar CSV para build
        state["step"] = "Exportando CSV"
        state["progress"] = 5
        state["log"].append("Exportando traducciones a CSV...")
        save()

        BUILD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = BUILD_TEMP_DIR / "dialogo.csv"
        count = export_csv_for_build(csv_path, only_translated=True)
        state["log"].append(f"  {count} textos exportados")

        # 3. Rebuild cada script
        total_scripts = len(script_ids)
        for i, sid in enumerate(script_ids):
            pct = 5 + int((i / total_scripts) * 60)
            state["step"] = f"Reconstruyendo script {sid} ({i+1}/{total_scripts})"
            state["progress"] = pct
            state["log"].append(f"[{i+1}/{total_scripts}] Script {sid}...")
            save()

            result = run_rebuild(sid, csv_path)
            if result["success"]:
                state["log"].append(f"  ✓ OK")
            else:
                state["log"].append(f"  ⚠ {result['stderr'][:100]}")
                # Continuar con otros scripts aunque uno falle

        # 4. Aplicar traducciones al ELF
        state["step"] = "Aplicando traducciones ELF"
        state["progress"] = 70
        state["log"].append("Ejecutando apply_translation.py...")
        save()

        elf_apply = run_apply_translation(csv_path)
        if elf_apply["success"]:
            state["log"].append("  ✓ ELF procesado")
        else:
            state["log"].append(f"  ⚠ ELF: {elf_apply['stderr'][:100]}")

        # 5. Reconstruir ISO
        state["step"] = "Generando ISO"
        state["progress"] = 80
        state["log"].append("Ejecutando build_iso.py...")
        save()

        iso_result = run_build_iso()
        if iso_result["success"]:
            state["log"].append("  ✓ ISO generada")
        else:
            state["log"].append(f"  ✗ Error: {iso_result['stderr'][:100]}")

        # 6. Inyectar ELF
        state["step"] = "Inyectando ELF"
        state["progress"] = 95
        state["log"].append("Ejecutando inject_elf.py...")
        save()

        elf_result = run_inject_elf()
        if elf_result["success"]:
            state["log"].append("  ✓ ELF inyectado")
        else:
            state["log"].append(f"  ✗ Error: {elf_result['stderr'][:100]}")

        # 6. Build history
        iso_path = str(WORK / "Strawberry_translated.iso")
        build_record = BuildHistory(
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status="success" if iso_result["success"] else "failed",
            build_type="full",
            iso_path=iso_path if iso_result["success"] else "",
            step="Completado",
            progress_pct=100,
        )
        session.add(build_record)
        session.commit()
        session.close()

        state["status"] = "success"
        state["progress"] = 100
        state["step"] = "Completado"
        state["iso_path"] = iso_path if iso_result["success"] else ""
        state["log"].append(f"\n✓ ISO lista: {iso_path}" if iso_result["success"] else "\n✗ Falló la generación de ISO")
        save()

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["log"].append(f"ERROR: {e}")
        save()
        # Guardar en build history
        try:
            session = get_session()
            build_record = BuildHistory(
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                status="failed",
                build_type="full",
                error_log=str(e)[:500],
                step=state.get("step", "error"),
                progress_pct=state.get("progress", 0),
            )
            session.add(build_record)
            session.commit()
            session.close()
        except Exception:
            pass
    finally:
        release_build_lock()

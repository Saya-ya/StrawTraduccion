"""Build pipeline — orquesta patch_dec.py + build_iso.py + inject_elf.py."""
import json
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

from ..config import (
    TIMEOUT_REBUILD, TIMEOUT_APPLY_ELF, TIMEOUT_BUILD_ISO, TIMEOUT_INJECT_ELF,
    BUILD_TEMP_DIR, BUILD_STATE_FILE, WORK
)
from ..database import get_session, TextEntry, Script, BuildHistory
from .build_lock import acquire_build_lock, release_build_lock, write_build_state

# ── Número de workers para compresión paralela ───────────────────────────────
COMPRESS_WORKERS = 4


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


def _rebuild_one_script(script_id: int, csv_path: Path, bin_path: Path, verify: bool = True) -> dict:
    """Reconstruye .dec, comprime, e inyecta en Data.bin (todo en un solo paso)."""
    result = subprocess.run(
        ["python3", str(PROJECT_ROOT / "tools" / "patch_dec.py"),
         "--id", str(script_id), "--rebuild", "--csv", str(csv_path),
         "--verify" if verify else "",
         "--out", str(bin_path)],
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


def _compress_only(script_id: int, csv_path: Path) -> dict:
    """
    Reconstruye .dec y comprime LZ77 sin inyectar en Data.bin.
    Retorna los bytes comprimidos + metadatos para la fase de inyección.
    Se ejecuta en un thread del pool (paralelizable).
    """
    # Importar aquí para no cargar en el proceso principal
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from script_rebuilder import default_dec_path, load_csv_rows, rebuild_local_slack
    from lz77 import compress, decompress

    dec_path = default_dec_path(script_id)
    if not dec_path.exists():
        return {"success": False, "script_id": script_id, "error": f"{dec_path} no existe"}

    dec_data = dec_path.read_bytes()
    rows = load_csv_rows(csv_path, script_id)

    try:
        rebuilt, report = rebuild_local_slack(dec_data, rows)
    except Exception as e:
        return {"success": False, "script_id": script_id, "error": str(e)}

    if report['needs_shift']:
        needs_info = []
        for seg in report['needs_shift'][:10]:
            needs_info.append(
                f"[needs_shift] 0x{seg['start']:X}: "
                f"requiere {seg['required_bytes']} / capacidad {seg['capacity_bytes']}"
            )
        return {
            "success": False,
            "script_id": script_id,
            "error": "needs_shift",
            "detail": "\n".join(needs_info),
            "report": report,
        }

    # Verify round-trip
    comp_data = compress(rebuilt)
    redec = decompress(comp_data)
    diffs = sum(a != b for a, b in zip(rebuilt, redec))
    if diffs > 0 or len(rebuilt) != len(redec):
        return {
            "success": False,
            "script_id": script_id,
            "error": f"Round-trip falló: {diffs} diffs, orig={len(rebuilt)}, redec={len(redec)}",
        }

    return {
        "success": True,
        "script_id": script_id,
        "comp_data": comp_data,
        "report": report,
    }


def _inject_one(script_id: int, comp_data: bytes, bin_path: Path) -> dict:
    """
    Inyecta datos comprimidos en Data.bin y actualiza la FAT.
    Se ejecuta secuencialmente (escritura al mismo archivo).
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    from datafat import read_entries, find_row, slot_capacity, size_field_write_offset

    rows = read_entries(str(bin_path))
    target = find_row(rows, script_id)
    if target is None:
        return {"success": False, "script_id": script_id, "error": "No encontrado en FAT"}

    foff = target['off']
    slot_size = slot_capacity(rows, target)

    if len(comp_data) > slot_size:
        return {
            "success": False,
            "script_id": script_id,
            "error": f"No cabe en slot ({len(comp_data):,} > {slot_size:,})",
        }

    with open(bin_path, 'r+b') as f:
        f.seek(foff)
        f.write(comp_data)
        f.write(b'\x00' * (slot_size - len(comp_data)))
        f.seek(size_field_write_offset(target))
        f.write(struct.pack('<I', len(comp_data)))

    return {"success": True, "script_id": script_id}


def run_apply_translation(csv_path: Path) -> dict:
    """Ejecuta apply_translation.py para generar SLPS_256.11_translated."""
    try:
        result = subprocess.run(
            ["python3", str(PROJECT_ROOT / "traduccion_tools" / "apply_translation.py"),
             str(csv_path)],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT),
            timeout=TIMEOUT_APPLY_ELF
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-300:],
            "stderr": result.stderr[-300:],
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Timeout tras {TIMEOUT_APPLY_ELF}s",
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


def _fresh_databin() -> Path:
    """Copia Data.bin limpio desde originales/ a work/Data_patched.bin."""
    src = PROJECT_ROOT / "originales" / "Data.bin"
    dst = WORK / "Data_patched.bin"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or src.stat().st_size != dst.stat().st_size:
        shutil.copy2(src, dst)
    return dst


def run_full_build(build_id: str):
    """Pipeline completo: export → parallel compress → sequential inject → ISO → ELF."""
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
            TextEntry.script_id != -1
        ).distinct().all()
        script_ids = sorted([s[0] for s in translated_scripts])

        if not script_ids:
            state["status"] = "failed"
            state["error"] = "No hay traducciones para build"
            save()
            session.close()
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

        # 3. Copia FRESCA de Data.bin (evita contaminación de builds anteriores)
        state["step"] = "Preparando Data.bin"
        state["progress"] = 8
        state["log"].append("Copiando Data.bin fresco desde originales/...")
        save()
        bin_path = _fresh_databin()
        state["log"].append(f"  Data.bin listo ({bin_path.stat().st_size // 1024 // 1024} MB)")

        # 4. Compresión paralela (Fase 1: solo CPU, sin escribir a Data.bin)
        total_scripts = len(script_ids)
        state["step"] = f"Comprimiendo scripts (paralelo, {COMPRESS_WORKERS} workers)"
        state["progress"] = 10
        state["log"].append(f"Comprimiendo {total_scripts} scripts en paralelo...")
        save()

        compressed: dict[int, bytes] = {}
        errors: list[str] = []

        t_start = time.time()
        with ThreadPoolExecutor(max_workers=COMPRESS_WORKERS) as pool:
            futures = {pool.submit(_compress_only, sid, csv_path): sid for sid in script_ids}
            done = 0
            for future in as_completed(futures):
                sid = futures[future]
                done += 1
                try:
                    result = future.result()
                except Exception as e:
                    errors.append(f"  ⚠ {sid}: excepción — {e}")
                    state["log"].append(f"[{done}/{total_scripts}] Script {sid}... ⚠ {e}")
                    save()
                    continue

                if result["success"]:
                    compressed[sid] = result["comp_data"]
                    pct = 10 + int((done / total_scripts) * 15)
                    state["progress"] = pct
                    state["log"].append(
                        f"[{done}/{total_scripts}] Script {sid} ✓ "
                        f"({len(result['comp_data']):,} bytes)"
                    )
                else:
                    detail = result.get("detail", result.get("error", ""))
                    errors.append(f"  ⚠ {sid}: {detail[:200]}")
                    state["log"].append(
                        f"[{done}/{total_scripts}] Script {sid} ⚠ {detail[:150]}"
                    )
                save()

        elapsed = time.time() - t_start
        state["log"].append(f"  Compresión completada en {elapsed:.1f}s")
        state["log"].append(f"  OK: {len(compressed)}, errores: {len(errors)}")

        # 5. Inyección secuencial (Fase 2: escritura a Data.bin, debe ser secuencial)
        state["step"] = "Inyectando datos en Data.bin"
        state["progress"] = 50
        state["log"].append("Inyectando scripts comprimidos en Data.bin...")
        save()

        injected = 0
        for sid in script_ids:
            if sid not in compressed:
                continue
            inj_result = _inject_one(sid, compressed[sid], bin_path)
            if inj_result["success"]:
                injected += 1
            else:
                state["log"].append(f"  ⚠ {sid}: {inj_result.get('error', 'falló inyección')[:150]}")
        state["log"].append(f"  Inyectados: {injected}/{len(compressed)}")

        # 6. Aplicar traducciones al ELF
        if not any("needs_shift" in e for e in errors):
            state["step"] = "Aplicando traducciones ELF"
            state["progress"] = 70
            state["log"].append("Ejecutando apply_translation.py...")
            save()

            elf_apply = run_apply_translation(csv_path)
            if elf_apply["success"]:
                state["log"].append("  ✓ ELF procesado")
            else:
                state["log"].append(f"  ⚠ ELF: {elf_apply['stderr'][:100]}")

        # 7. Reconstruir ISO
        state["step"] = "Generando ISO"
        state["progress"] = 80
        state["log"].append("Ejecutando build_iso.py...")
        save()

        iso_result = run_build_iso()
        if iso_result["success"]:
            state["log"].append("  ✓ ISO generada")
        else:
            state["log"].append(f"  ✗ Error: {iso_result['stderr'][:100]}")

        # 8. Inyectar ELF
        state["step"] = "Inyectando ELF"
        state["progress"] = 95
        state["log"].append("Ejecutando inject_elf.py...")
        save()

        elf_result = run_inject_elf()
        if elf_result["success"]:
            state["log"].append("  ✓ ELF inyectado")
        else:
            state["log"].append(f"  ✗ Error: {elf_result['stderr'][:100]}")

        # 9. Build history
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

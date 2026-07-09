import os
import json
from pathlib import Path
from datetime import datetime, timezone

from ..config import BUILD_LOCK_FILE, BUILD_STATE_FILE


def acquire_build_lock() -> bool:
    if BUILD_LOCK_FILE.exists():
        try:
            data = json.loads(BUILD_LOCK_FILE.read_text())
            pid = data.get('pid', 0)
            if pid:
                try:
                    os.kill(pid, 0)
                    return False
                except OSError:
                    pass
        except (json.JSONDecodeError, ValueError):
            pass

    BUILD_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUILD_LOCK_FILE.write_text(json.dumps({
        'pid': os.getpid(),
        'started_at': datetime.now(timezone.utc).isoformat(),
    }))
    return True


def release_build_lock():
    if BUILD_LOCK_FILE.exists():
        BUILD_LOCK_FILE.unlink()


def is_build_running() -> bool:
    if not BUILD_LOCK_FILE.exists():
        return False
    try:
        data = json.loads(BUILD_LOCK_FILE.read_text())
        pid = data.get('pid', 0)
        if pid:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                BUILD_LOCK_FILE.unlink()
                return False
    except (json.JSONDecodeError, ValueError):
        BUILD_LOCK_FILE.unlink()
        return False
    return False


def get_build_state() -> dict:
    if BUILD_STATE_FILE.exists():
        try:
            return json.loads(BUILD_STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {"status": "idle"}


def write_build_state(state: dict):
    BUILD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUILD_STATE_FILE.write_text(json.dumps(state))

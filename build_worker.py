#!/usr/bin/env python3
"""Worker process for running the build pipeline independently.

Usage: python build_worker.py <build_id> [build_type] [workers]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from webapp.services.builder import run_full_build

if __name__ == "__main__":
    build_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    build_type = sys.argv[2] if len(sys.argv) > 2 else "full"
    try:
        workers = int(sys.argv[3]) if len(sys.argv) > 3 else None
    except ValueError:
        workers = None
    run_full_build(build_id, build_type=build_type, workers=workers)

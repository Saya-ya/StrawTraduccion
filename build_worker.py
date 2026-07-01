#!/usr/bin/env python3
"""Worker process for running the build pipeline independently.

Usage: python build_worker.py <build_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from webapp.services.builder import run_full_build

if __name__ == "__main__":
    build_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    run_full_build(build_id)

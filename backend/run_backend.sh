#!/usr/bin/env bash
# Start the Sterling backend.
#
# Production launch uses one uvicorn worker and no --reload. One worker is
# required because health heartbeats, alert de-duplication and ops-scheduler
# state are in-process; multi-process operation needs validated cross-process
# locking first.
#
# The launcher intentionally has no shell .env parser. The Python process loads
# the same STERLING_* namespace as direct `python3 main.py`, then replaces
# itself with uvicorn. If environment loading/import fails, this process exits
# non-zero and uvicorn is never started.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

exec ./.venv/bin/python - <<'PY'
from pathlib import Path
import os

from app.core.runtime_env import load_sterling_env

# Fail closed: any unexpected parser/import failure terminates this launcher.
load_sterling_env()

host = (os.environ.get("STERLING_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
uvicorn = str(Path(".venv/bin/uvicorn").resolve())
argv = [
    uvicorn,
    "main:app",
    "--host", host,
    "--port", "8000",
    "--workers", "1",
    "--timeout-graceful-shutdown", "20",
]

# execve explicitly carries the environment mutated by load_sterling_env().
os.execve(uvicorn, argv, os.environ)
PY

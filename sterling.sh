#!/bin/sh
# One-command Sterling launcher.
# Resolves the project venv Python first; falls back to system python3.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$ROOT/backend/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=$(command -v python3)
exec "$PYTHON" "$ROOT/scripts/sterling_stack.py" "$@"

#!/usr/bin/env bash
# Start the Sterling backend for the frozen Snapback prospective runtime.
#
# Production launch: a single uvicorn worker, no --reload. One worker is required
# because the health heartbeats, alert de-duplication and ops-scheduler state are
# in-process; multi-process workers would each keep their own copy and would need
# validated cross-process locking first.
#
# Import STERLING_* through the same parser used by `python3 main.py`. Keeping a
# second shell parser here made quoted values and safety switches mean different
# things depending only on the launch command. Non-Sterling settings remain under
# pydantic-settings.
set -euo pipefail

cd "$(dirname "$0")"

while IFS= read -r -d '' assignment; do
  export "$assignment"
done < <(
  ./.venv/bin/python - <<'PY'
import os
import sys

from app.core.runtime_env import load_sterling_env

for key in load_sterling_env():
    sys.stdout.write(f"{key}={os.environ[key]}\0")
PY
)

mkdir -p logs

# Loopback by default. Binding every interface on a home network exposes an
# authenticated broker session to whatever else is on the Wi-Fi; opening it is a
# deliberate act, so it takes STERLING_BIND_HOST.
exec ./.venv/bin/uvicorn main:app \
  --host "${STERLING_BIND_HOST:-127.0.0.1}" \
  --port 8000 \
  --workers 1

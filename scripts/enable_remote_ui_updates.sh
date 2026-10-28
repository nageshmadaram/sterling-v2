#!/usr/bin/env bash
# One-time enrollment: follow reviewed origin/main for the STATIC FRONTEND only.
# Does not merge checkout branches, alter uncommitted files, change Kite/broker
# settings, or restart a backend responsible for orders and protective stops.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
cd "$ROOT"
umask 077
if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
  echo "BLOCKED: expected existing regular backend/.env; refusing to create or follow links." >&2
  exit 2
fi
if ! git rev-parse --git-dir >/dev/null; then
  echo "BLOCKED: Sterling checkout missing Git metadata." >&2
  exit 2
fi
if ! git remote get-url origin >/dev/null; then
  echo "BLOCKED: origin remote missing." >&2
  exit 2
fi

# A credential/network failure must not flip the publisher to an unfetchable
# ref or consume a Git worktree containing uncommitted changes.
git fetch --no-tags origin main >&2
git rev-parse --verify 'refs/remotes/origin/main^{commit}' >/dev/null
ROOT="$ROOT" python3 - <<'PY'
import os
from pathlib import Path
import tempfile

root = Path(os.environ["ROOT"])
target = root / "backend" / ".env"
lines = target.read_text().splitlines(keepends=True)
updates = {"STERLING_PUBLISH_REF": "origin/main", "STERLING_PUBLISH_FETCH": "1"}
output = []
seen = set()
for line in lines:
    key, sep, _ = line.partition("=")
    key = key.strip()
    if sep and key in updates:
        if key not in seen:
            output.append(f"{key}={updates[key]}\n")
            seen.add(key)
        # Drop duplicate declarations to avoid accidentally disabling the
        # feature with a second, conflicting value further down the file.
    else:
        output.append(line)
if output and not output[-1].endswith("\n"):
    output.append("\n")
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}\n")
fd, temp = tempfile.mkstemp(prefix=".env.update.", dir=target.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.writelines(output)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, target)
finally:
    if os.path.exists(temp):
        os.unlink(temp)
print("Remote UI publishing enrolled: origin/main, fetch enabled.")
print("Trading backend, secrets, local branches and working files unchanged.")
PY

# Existing presentation manager reads its environment at startup. Do not
# automatically terminate it: a managed tunnel might change a temporary URL.
# Next approved presentation-only restart picks up this setting.
echo "Activation: restart ONLY the Sterling presentation manager once, preserving the trading backend."
echo "The supervisor then checks reviewed main every ~60 seconds and atomically publishes successful frontend builds."
echo "To check: ./sterling.sh status (release_ref, release_commit, release_stale)"

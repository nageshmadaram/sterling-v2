#!/usr/bin/env bash
# Git hooks record deployment work; they never change a running trading engine.
# The supervised web publisher follows committed frontend revisions. Backend and
# dependency promotion requires explicit controlled restart/reconciliation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.runtime/stack"
umask 077
mkdir -p "$STATE"
exec 9>"$STATE/deploy-notice.lock"
flock -x 9
REVISION=$(git -C "$ROOT" rev-parse HEAD)
CHANGED=$(git -C "$ROOT" diff --name-only HEAD~1 HEAD 2>/dev/null || git -C "$ROOT" ls-tree -r --name-only HEAD)
if [[ "$CHANGED" == *backend/* || "$CHANGED" == *deploy/systemd/* ]]; then
  printf '%s\n' "$REVISION" > "$STATE/backend-deploy-required.next"
  mv "$STATE/backend-deploy-required.next" "$STATE/backend-deploy-required"
  printf '%s [deploy] Backend promotion pending for %s; running engine unchanged.\n' "$(date -Is)" "$REVISION"
fi
printf '%s [deploy] Commit %s available to supervised frontend publisher.\n' "$(date -Is)" "$REVISION"

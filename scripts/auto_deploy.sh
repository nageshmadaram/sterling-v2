#!/usr/bin/env bash
# auto_deploy.sh — Smart incremental rebuild triggered by git hooks.
#
# Detects what changed in the last commit/merge and:
#   • frontend/* changes → npm build only, no backend restart
#   • backend/* changes  → graceful uvicorn reload (SIGHUP), no rebuild
#   • both               → build then reload
#   • requirements.txt   → pip install + reload
#
# Runs entirely in the background so git operations never block.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/.runtime/stack/auto_deploy.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [deploy] $*" | tee -a "$LOG"; }

# ── What changed? ───────────────────────────────────────────────────────────
CHANGED=$(git -C "$ROOT" diff --name-only HEAD~1 HEAD 2>/dev/null || \
          git -C "$ROOT" diff --name-only ORIG_HEAD HEAD 2>/dev/null || \
          git -C "$ROOT" show --name-only --format="" HEAD 2>/dev/null || true)

FRONTEND_CHANGED=false
BACKEND_CHANGED=false
DEPS_CHANGED=false

while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  case "$f" in
    frontend/*)           FRONTEND_CHANGED=true ;;
    backend/requirements* ) DEPS_CHANGED=true; BACKEND_CHANGED=true ;;
    backend/*)            BACKEND_CHANGED=true ;;
  esac
done <<< "$CHANGED"

log "Changed files detected → frontend=$FRONTEND_CHANGED backend=$BACKEND_CHANGED deps=$DEPS_CHANGED"

# ── Nothing relevant changed ────────────────────────────────────────────────
if ! $FRONTEND_CHANGED && ! $BACKEND_CHANGED; then
  log "No frontend or backend changes — skipping deploy."
  exit 0
fi

# ── Frontend rebuild ────────────────────────────────────────────────────────
if $FRONTEND_CHANGED; then
  log "Rebuilding frontend…"
  cd "$ROOT/frontend"
  npm run build >> "$LOG" 2>&1 && log "Frontend build ✓" || { log "Frontend build FAILED"; exit 1; }
fi

# ── Backend: pip install if requirements changed ─────────────────────────────
if $DEPS_CHANGED; then
  log "Installing updated Python dependencies…"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt" >> "$LOG" 2>&1 \
    && log "pip install ✓" || log "pip install FAILED (non-fatal)"
fi

# ── Backend: graceful reload via SIGHUP ─────────────────────────────────────
if $BACKEND_CHANGED; then
  UVICORN_PID=$(pgrep -f "uvicorn main:app" 2>/dev/null | head -1 || true)
  if [[ -n "$UVICORN_PID" ]]; then
    log "Sending SIGHUP to uvicorn (pid=$UVICORN_PID) for graceful reload…"
    kill -HUP "$UVICORN_PID" && log "Backend reload ✓" || log "Backend reload signal failed"
  else
    log "Uvicorn not running — skipping reload signal."
  fi
fi

log "Auto-deploy complete."

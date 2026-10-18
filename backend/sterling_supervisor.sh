#!/usr/bin/env bash
# Liveness supervisor for the Sterling backend.
#
# Starts the backend and probes a cheap endpoint every 60 seconds. A process that
# merely exists is not healthy: the previous outage had a live worker that answered
# nothing. Three consecutive connection/timeout failures restart the backend.
set -uo pipefail

cd "$(dirname "$0")"

# Liveness must stay independent of broker, database and readiness failures.
PROBE_URL="${STERLING_PROBE_URL:-http://127.0.0.1:8000/api/v1/health/live}"
PROBE_INTERVAL="${STERLING_PROBE_INTERVAL:-60}"
PROBE_TIMEOUT="${STERLING_PROBE_TIMEOUT:-30}"
MAX_FAILURES="${STERLING_PROBE_MAX_FAILURES:-3}"
STARTUP_GRACE="${STERLING_STARTUP_GRACE:-120}"

mkdir -p logs
LOG=logs/supervisor.log

log() { echo "$(date -Is) [supervisor] $*" | tee -a "$LOG"; }

BACKEND_PID=""

# Detect child death during grace and probe intervals, rather than trusting a
# listener that may belong to another launcher. wait records the actual status.
monitor_wait() {
  local remaining="$1" status
  while (( remaining > 0 )); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      wait "$BACKEND_PID"; status=$?
      log "backend exited pid=$BACKEND_PID status=$status"
      return 1
    fi
    sleep 1
    remaining=$((remaining - 1))
  done
}

start_backend() {
  ./run_backend.sh >> logs/backend.log 2>&1 &
  BACKEND_PID=$!
  log "started backend pid=$BACKEND_PID; grace ${STARTUP_GRACE}s"
  monitor_wait "$STARTUP_GRACE" || true
}

# RT-01 fix: use SIGTERM first with a 30-second grace period so the Python
# lifespan handler can run record_shutdown(), checkpoint WAL, and cancel tasks.
# SIGKILL only as last resort. Previous pkill -9 caused instant termination
# with no shutdown record, no WAL checkpoint, and no traceback.
stop_backend() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    log "sending SIGTERM to backend pid=$BACKEND_PID"
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$BACKEND_PID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$BACKEND_PID" 2>/dev/null; then
      log "backend did not stop gracefully after 30s; escalating to SIGKILL"
      kill -9 "$BACKEND_PID" 2>/dev/null || true
    fi
  fi
  sleep 2
}

trap 'log "supervisor stopping"; stop_backend; exit 0' INT TERM

start_backend

failures=0
while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID"; status=$?
    log "backend exited pid=$BACKEND_PID status=$status; restarting"
    sleep 2
    start_backend
    failures=0
    continue
  fi
  if curl -fsS -m "$PROBE_TIMEOUT" -o /dev/null "$PROBE_URL"; then
    if (( failures > 0 )); then
      log "probe recovered after ${failures} failure(s)"
    fi
    failures=0
  else
    failures=$((failures + 1))
    log "probe FAILED (${failures}/${MAX_FAILURES}) url=$PROBE_URL"
    if (( failures >= MAX_FAILURES )); then
      log "restarting backend after ${failures} consecutive probe failures"
      stop_backend
      start_backend
      failures=0
    fi
  fi
  monitor_wait "$PROBE_INTERVAL" || true
done

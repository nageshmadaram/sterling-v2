#!/usr/bin/env bash
# Offline Snapback acceptance slice. No broker, Docker, CI or live orders.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
repo_root="$PWD"
if [[ ! -d backend/tests/engines/snapback || ! -x backend/.venv/bin/python ]]; then
  echo "BLOCKED: backend source or virtualenv unavailable" >&2
  exit 2
fi
if [[ "$(git branch --show-current)" != "codex/implementation-spec-20261026" ]]; then
  echo "BLOCKED: run the draft implementation branch; do not test the wrong SHA" >&2
  exit 2
fi
head="$(git rev-parse HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "BLOCKED: tracked files differ from tested HEAD; save changes before acceptance" >&2
  exit 2
fi
if [[ "${STERLING_LIVE_EXECUTION:-false}" =~ ^([1]|[Yy][Ee][Ss]|[Tt][Rr][Uu][Ee])$ ]]; then
  echo "BLOCKED: live-execution environment must be OFF during broker-free tests" >&2
  exit 2
fi

scratch="$(mktemp -d -t snapback-offline-XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
export ENVIRONMENT=test
export PAPER_TRADING=true
export REAL_PUBLIC_DATA=false
export STERLING_LIVE_EXECUTION=false
export PYTHONDONTWRITEBYTECODE=1
export PYTHONWARNINGS=ignore
export STERLING_INTENT_DB="$scratch/intents.db"
export STERLING_EVIDENCE_DB="$scratch/evidence.db"
export STERLING_DB_PATH="$scratch/sterling.db"
cd backend

echo "SNAPBACK OFFLINE ACCEPTANCE; SHA $head"
echo "This is a source/test readiness probe, NOT a Kite or profitability certificate."
set +e
env timeout 240s .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  tests/engines/snapback/test_intraday.py \
  tests/engines/snapback/test_intraday_economics.py \
  tests/engines/snapback/test_walkforward.py \
  tests/engines/snapback/test_challenger_cost_feasible.py \
  tests/engines/snapback/test_research_reporting.py \
  tests/unit/test_research_coverage.py
research_rc=$?
env timeout 240s .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  tests/unit/test_snapback_limit_entry_authority.py \
  tests/unit/test_snapback_booking_authority.py \
  tests/unit/test_snapback_live_executor.py \
  tests/unit/test_snapback_live_partial_fills.py
execution_rc=$?
env timeout 240s .venv/bin/python -m pytest -q -p no:cacheprovider --tb=short \
  tests/unit/test_snapback_exit_resolution_boundary.py \
  tests/unit/test_snapback_live_exits.py \
  tests/unit/test_snapback_reconciliation.py \
  tests/unit/test_snapback_unknown_order_recovery.py
recovery_rc=$?
set -e
cd "$repo_root"
printf 'SOURCE_SHA=%s\nRESEARCH_EXIT=%s\nEXECUTION_EXIT=%s\nRECOVERY_EXIT=%s\n' \
  "$head" "$research_rc" "$execution_rc" "$recovery_rc"
if (( research_rc != 0 || execution_rc != 0 || recovery_rc != 0 )); then
  echo "BLOCKED: one or more Snapback offline suites failed. No promotion."
  exit 1
fi
echo "PASS: offline source tests only. Still NOT broker/economic certified."

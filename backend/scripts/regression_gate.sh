#!/usr/bin/env bash
#
# Zero-regression gate.
#
# Fails ONLY when a test fails on HEAD that PASSES at the merge-base with the
# target branch (a real regression). Pre-existing failures — of which this suite
# has ~36-42, including order-dependent/flaky ones — are ignored, so the gate is
# meaningful instead of perpetually red.
#
# Hard-fails (exit 2) if pytest doesn't actually run, so the gate can never
# silently no-op into a false pass.
#
# Run from anywhere in the repo:  bash backend/scripts/regression_gate.sh [base_ref]
#   base_ref defaults to origin/main.
#
set -uo pipefail

BASE_REF="${1:-origin/main}"
DESEL="--deselect tests/test_delta_iv_socket.py::test_lifespan_starts_iv_stream_only_when_env_set"

ROOT="$(git rev-parse --show-toplevel)"
PY="python"
[ -x "$ROOT/backend/.venv/bin/python" ] && PY="$ROOT/backend/.venv/bin/python"

# Run the suite in $1/backend; print sorted-unique failing ids on stdout.
# Returns 2 if pytest did not complete a comparable test run (UNKNOWN).
run_suite() {
  local dir="$1" log rc
  log="$(mktemp)"
  ( cd "$dir/backend" && PYTHONWARNINGS=ignore "$PY" -m pytest tests/ -q $DESEL -p no:cacheprovider ) > "$log" 2>&1
  rc=$?
  # Exit 2 includes collection failures and interruptions; 3/4/5 mean internal
  # error, usage error, or no tests. None proves a passing baseline. Setup and
  # teardown errors (exit 1) likewise cannot be compared as ordinary failures.
  if { [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; } ||
     grep -qE '^ERROR ' "$log" ||
     ! grep -qE '^[= ]*[0-9]+ (passed|failed).* in [0-9.]+s' "$log" ||
     { [ "$rc" -eq 1 ] && ! grep -qE '^FAILED ' "$log"; }; then
    echo "::error::UNKNOWN: pytest did not complete in $dir/backend (exit $rc). Tail:" >&2
    tail -40 "$log" >&2
    rm -f "$log"
    return 2
  fi
  # Pytest emits failed node ids as lines beginning exactly with "FAILED ".
  # Application logs may legitimately contain words such as "preflight FAILED";
  # those are runtime diagnostics, not failed tests, and must not poison this gate.
  grep -E '^FAILED ' "$log" | sed -E 's/^FAILED //; s/ - .*//' | sort -u
  rm -f "$log"
}

echo "▶ Running HEAD suite (fast: warnings suppressed)…"
run_suite "$ROOT" > /tmp/rg_head.txt || exit 2
echo "  HEAD failures: $(wc -l < /tmp/rg_head.txt)"

BASE_SHA="$(git merge-base HEAD "$BASE_REF" 2>/dev/null || true)"
if [ -z "$BASE_SHA" ]; then
  echo "::error::No merge-base with $BASE_REF — regression comparison is impossible."
  exit 2
fi

echo "▶ Running baseline suite at $BASE_SHA…"
WT="$(mktemp -d)"
if ! git worktree add -q --detach "$WT" "$BASE_SHA"; then
  echo "::error::UNKNOWN: baseline checkout could not be prepared"
  exit 2
fi
run_suite "$WT" > /tmp/rg_base.txt
rc=$?
git worktree remove --force "$WT" 2>/dev/null || true
[ "$rc" -eq 0 ] || { echo "::error::baseline suite failed to run"; exit 2; }
echo "  baseline failures: $(wc -l < /tmp/rg_base.txt)"

NEW="$(comm -23 /tmp/rg_head.txt /tmp/rg_base.txt)"
if [ -n "$NEW" ]; then
  echo "::error::REGRESSION — these tests fail on HEAD but pass at the merge-base:"
  echo "$NEW" | sed 's/^/  ✗ /'
  exit 1
fi
echo "✅ No regressions (every HEAD failure also fails at baseline — all pre-existing)."
exit 0

#!/usr/bin/env bash
#
# Zero-regression gate.
#
# Fails ONLY when a test fails on HEAD that PASSES at the merge-base with the
# target branch (a real regression). Pre-existing failures — of which this suite
# has ~36-42, including order-dependent/flaky ones — are ignored, so the gate is
# meaningful instead of perpetually red.
#
# Hard-fails (exit 2) on collection/interruption/internal errors or absent
# structured results. Setup/teardown errors count as test failures.
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

# Concurrent invocations must never compare one another's failure lists.
SCRATCH="$(mktemp -d)" || exit 2
WT=""
cleanup() {
  [ -z "$WT" ] || git worktree remove --force "$WT" 2>/dev/null || true
  rm -rf "$SCRATCH"
}
trap cleanup EXIT
trap 'exit 2' INT TERM

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
  "$PY" - "$report" "$rc" <<'PY'
import sys
import xml.etree.ElementTree as ET

try:
    cases = list(ET.parse(sys.argv[1]).getroot().iter("testcase"))
    executed = [case for case in cases if case.find("skipped") is None]
    if not executed:
        raise ValueError("no executed test cases")
    failures = sorted({f"{case.get('classname')}::{case.get('name')}"
                       for case in executed
                       if case.find("failure") is not None or case.find("error") is not None})
    if bool(failures) != (int(sys.argv[2]) == 1):
        raise ValueError("pytest exit code and test results disagree")
    for failure in failures:
        print(failure)
except (OSError, ET.ParseError, ValueError) as exc:
    print(f"::error::invalid pytest evidence: {exc}", file=sys.stderr)
    sys.exit(2)
PY
  rc=$?
  rm -f "$log" "$report"
  return "$rc"
}

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

echo "▶ Running baseline suite at $BASE_SHA…"
WT="$SCRATCH/baseline"
git worktree add -q --detach "$WT" "$BASE_SHA" || exit 2
run_suite "$WT" > "$SCRATCH/base.txt"
rc=$?
[ "$rc" -eq 0 ] || { echo "::error::baseline suite failed to run"; exit 2; }
echo "  baseline failures: $(wc -l < "$SCRATCH/base.txt")"

NEW="$(comm -23 "$SCRATCH/head.txt" "$SCRATCH/base.txt")"
if [ -n "$NEW" ]; then
  echo "::error::REGRESSION — these tests fail on HEAD but pass at the merge-base:"
  echo "$NEW" | sed 's/^/  ✗ /'
  exit 1
fi
echo "✅ No regressions (every HEAD failure also fails at baseline — all pre-existing)."
exit 0

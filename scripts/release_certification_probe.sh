#!/usr/bin/env bash
# Non-trading exact-commit evidence probe. Never places orders, runs dangerous
# failure drills, changes safe mode, exposes credentials or restarts services.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)" || exit 2
cd "$ROOT" || exit 2
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$HOME/sterling-release-probe-$STAMP"
mkdir -p "$REPORT" || exit 2
chmod 700 "$REPORT"
SUMMARY="$REPORT/summary.txt"
: > "$SUMMARY"
SHA="$(git rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
printf 'candidate_sha=%s\nrun_utc=%s\n' "$SHA" "$STAMP" >> "$SUMMARY"
record() {
  printf '%s=%s | %s\n' "$1" "$2" "$3" >> "$SUMMARY"
  printf '%s: %s (%s)\n' "$1" "$2" "$3"
}
blocked() { record "$1" "BLOCKED" "$2"; }
check() {
  local label="$1"; shift
  ( "$@" ) > "$REPORT/$label.log" 2>&1
  local status=$?
  if (( status == 0 )); then
    case "$label" in
      operator_bindings|operator_exposure)
        # Exit zero means inventory could be READ, not that a fresh usable
        # binding or reconciled zero-exposure account has been proven.
        record "$label" OBSERVED "read-only inventory returned exit=0; see private log"
        ;;
      *)
        record "$label" PASS "exit=0"
        ;;
    esac
  else
    record "$label" FAIL "exit=$status, see private log"
    # Build and test logs can contain credentials, paths and broker identity,
    # not only logs from operator_ commands. Keep every raw log in the private
    # report directory; do not replay excerpts to a shared terminal.
  fi
}
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  blocked clean_tree "Uncommitted changes invalidate an exact-SHA claim"
else record clean_tree PASS "$SHA"; fi
if [[ "$(git rev-parse main)" == "$(git rev-parse dev)" ]]; then
  record branch_alignment PASS "main=dev"
else blocked branch_alignment "main and dev differ"; fi
if [[ "$SHA" == "$(git rev-parse origin/main 2>/dev/null)" ]] &&
   [[ "$SHA" == "$(git rev-parse origin/dev 2>/dev/null)" ]]; then
  record remote_identity PASS "checked-out SHA matches both fetched remotes"
else
  blocked remote_identity "candidate is not the current fetched main/dev SHA"
fi

PYTHON="$ROOT/backend/.venv/bin/python"
test_group() {
  local label="$1"; shift
  local isolated
  isolated="$(mktemp -d "$REPORT/test-state.XXXXXXXX")" || return
  (
    cd "$ROOT/backend" || exit 2
    # A release probe may be invoked by a shell carrying actual Kite tokens,
    # Telegram bot credentials, production secret keys and live state paths.
    # Unit/integration mocks must never inherit those values. This scrub is
    # scoped ONLY to test_group's subshell: the subsequent read-only operator
    # inspections still use the real configured runtime.
    for inherited in ${!STERLING_@} ${!KITE_@} ${!ZERODHA_@} ${!TELEGRAM_@}; do
      unset "$inherited"
    done
    unset ENVIRONMENT
    export ENVIRONMENT=development
    export STERLING_LIVE_EXECUTION=0
    export STERLING_STATE_ROOT="$isolated"
    export STERLING_DB_PATH="$isolated/sterling.db"
    export STERLING_OBSERVATIONS_DB_PATH="$isolated/observations.db"
    export STERLING_EVIDENCE_DB="$isolated/observations.db"
    "$PYTHON" -m pytest -q "$@"
  ) > "$REPORT/$label.log" 2>&1
  local status=$?
  rm -rf -- "$isolated"
  if (( status == 0 )); then
    record "$label" PASS "isolated regression suite passed"
  else
    record "$label" FAIL "exit=$status, see private log"
  fi
}

if [[ -x "$PYTHON" ]]; then
  test_group snapback_lifecycle \
    tests/engines/snapback \
    tests/services/test_snapback_sessions.py \
    tests/services/test_snapback_validation.py \
    tests/services/test_snapback_replay_risk.py \
    tests/integration/test_snapback_execution_contract_path.py \
    tests/integration/test_snapback_evidence_lifecycle.py \
    tests/unit/test_snapback_protection_and_exit.py \
    tests/unit/test_snapback_safe_mode_and_audit.py
  test_group gamma_unwind_lifecycle \
    tests/unit/test_gamma_unwind_reference.py \
    tests/services/test_gamma_move_production.py \
    tests/services/test_gamma_move_live_audit.py \
    tests/services/test_gamma_recovery.py \
    tests/services/test_gamma_move_alerts.py \
    tests/e2e_live/test_gamma_reconcile_e2e.py
  test_group supertrend_lifecycle \
    tests/engines/sterling_kite_engine/test_supertrend_final_audit.py \
    tests/unit/test_supertrend_core_freeze_audit.py \
    tests/unit/test_supertrend_parity_freeze.py
  test_group telegram_lifecycle \
    tests/services/test_telegram_alert_delivery.py \
    tests/services/test_telegram_lifecycle_outbox.py \
    tests/unit/test_snapback_alert_telegram_sink.py
  test_group broker_backup_safety \
    tests/unit/test_production_settings.py \
    tests/unit/test_sterlingctl_environment.py \
    tests/unit/test_load_sterling_env.py \
    tests/unit/test_broker_binding_verification.py \
    tests/unit/test_release_operational_gates.py \
    tests/unit/test_release_certification_authority.py \
    tests/unit/test_release_backup_gate.py \
    tests/unit/test_backup_manifest_restore_proof.py \
    tests/unit/test_full_backup_contents.py \
    tests/unit/test_release_exposure_freshness.py \
    tests/unit/test_snapback_preflight.py::test_preflight_rejects_nonfinite_approved_capital \
    tests/unit/test_snapback_preflight.py::test_preflight_accepts_a_real_positive_finite_capital \
    tests/unit/test_safety_wiring_end_to_end.py::test_nonfinite_limits_fail_closed \
    tests/unit/test_safety_wiring_end_to_end.py::test_nonfinite_risk_request_fails_closed \
    tests/unit/test_safety_wiring_end_to_end.py::test_nonfinite_used_exposure_fails_closed \
    tests/unit/test_operator_report.py::test_supertrend_modes_are_declared_but_all_follow_the_global_kill_switch \
    tests/unit/test_operator_report.py::test_dashboard_reports_undefined_rules_when_a_lane_is_unqualified \
    tests/unit/test_operator_report.py::test_rendering_names_the_state_and_the_reason \
    tests/unit/test_operator_report.py::test_global_only_risk_hierarchy_is_incomplete_not_ready \
    tests/unit/test_operator_report.py::test_a_complete_live_lane_risk_hierarchy_passes \
    tests/unit/test_operator_report.py::test_a_missing_live_lane_cap_fails_doctor_with_scope
else blocked backend_tests "Python virtual environment missing"; fi

if [[ -x "$ROOT/frontend/node_modules/.bin/vitest" ]]; then
  check frontend_build bash -c 'cd "$1/frontend" && npm run build' -- "$ROOT"
  check frontend_regressions bash -c \
    'cd "$1/frontend" && npm test -- --run src/pages/__tests__/SimpleTerminal.tv.test.tsx src/components/sim/__tests__/ViewSwitch.test.tsx src/components/tv/__tests__/TvWorkspace.test.tsx tests/tvIntegrationSecurity.test.ts' \
    -- "$ROOT"
else blocked frontend "Dependencies missing"; fi
if command -v node >/dev/null 2>&1; then
  check tv_gateway_node node --test scripts/tests/tv_gateway.test.mjs
else blocked tv_gateway_node "Node unavailable"; fi

# These operator commands are observation-only. Some record local diagnostic
# telemetry but they do not submit broker orders or toggle live entry.
if [[ -f scripts/sterlingctl ]]; then
  for subcommand in doctor bindings egress exposure drills; do
    check "operator_$subcommand" bash scripts/sterlingctl "$subcommand"
  done
  # Unlike the historical advisory "certify", this command really fails when
  # a required release gate or exposure fact is UNKNOWN/FAIL.
  check operator_certify_strict bash scripts/sterlingctl certify strict
else blocked operator_cli "sterlingctl missing"; fi

FREE_KIB="$(df -Pk "$ROOT" | awk 'NR==2{print $4}')"
if [[ "$FREE_KIB" =~ ^[0-9]+$ ]] && (( FREE_KIB >= 10485760 )); then
  record disk_reserve CHECK "at least 10 GiB available; check actual volume policy"
else
  blocked disk_reserve "less than 10 GiB free or unreadable; no on-root backup attempted"
fi

# Real-world evidence cannot be generated from simulated/mock unit tests.
blocked broker_whitelist "Confirm stable IP and approved redirect in actual Kite developer console"
blocked fresh_broker_binding "Fresh account/profile/positions/orders/GTT proof required for this exact release"
blocked moving_market_reconnect "Owner-controlled read-only market reconnect drill required"
blocked real_entry_fill_protection_trail_exit "Separately authorized real-broker receipts required; no real order placed"
blocked telegram_phone_receipt "Provider acknowledgement and actual destination receipt required"
blocked full_backup_isolated_restore "Full physical manifests and independent restore proof required; not attempted"
blocked production_operator_handover "Current candidate tag and operator acceptance required"

printf '\n=== CERTIFICATION EVIDENCE ===\n'
cat "$SUMMARY"
printf '\nPrivate report directory: %s\n' "$REPORT"
echo "No live-order approval is granted by this probe."
# A status of zero would be easy to misread as an approved release. Be explicit:
# until real-world evidence gates are satisfied, certification fails closed.
if grep -Eq '=(BLOCKED|FAIL) ' "$SUMMARY"; then
  echo "RELEASE CERTIFICATION: BLOCKED (see summary for evidence requirements)"
  exit 1
fi
echo "RELEASE CERTIFICATION: PROBE COMPLETE; independent operational acceptance still required"

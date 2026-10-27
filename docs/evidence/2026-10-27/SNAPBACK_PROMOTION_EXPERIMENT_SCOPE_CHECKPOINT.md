# Snapback prospective promotion must belong to one experiment and build

Date: 2026-10-27. Branch: `dev`. **Not a live-trading or profitability certificate.**

## Issue corrected

The authoritative `PromotionService.evaluate` previously used
`observed_session_count(warehouse)`, which counted *all* fully observed
session-ledger rows in the warehouse. A current experiment with 20 complete
sessions and an old experiment with 40 complete sessions could appear to
satisfy the frozen 60-session requirement.

Worse, the completed-trade input builder selected records by build SHA and
authority but did not prove that each completed outcome's Day-T opportunity
actually arose in a session owned by the **current experiment**. A stale
historical opportunity could therefore contribute toward the 300 completed
trades if it shared an executable build SHA.

These are provenance gaps, not a reason to relax the forward-profitability
gate. A plausible backtest or a pooled mixed-experiment sample is not
independent prospective profit evidence.

## Changes

1. `snapback_session_ledger.observed_session_count` now has optional
   `experiment_id` and `runtime_build_sha` scopes. Its existing
   unscoped use remains available to diagnostic reports. The authoritative
   promotion service supplies **both** scope values.
2. The promotion service refuses completed sessions from a different
   runtime build inside the current experiment and reports the affected
   dates with an `INCONCLUSIVE` verdict.
3. `snapback_promotion_scope.out_of_scope_trade_errors` checks that each
   completed outcome has exactly one Day-T opportunity whose observed
   `signal_timestamp` date belongs to a session in the current experiment
   and executable build. Missing, malformed, ambiguous and foreign
   records are errors; bad records are *not* dropped to make the sample pass.
4. Updated `scripts/snapback_offline_acceptance.sh` to include
   the scoped session ledger, trade-origin and service regression tests.
   The existing single authoritative economic gate and thresholds are
   unchanged.

## Verified evidence

- `backend/app/services/snapback_promotion_scope.py` committed blob:
  `54b38b28f85d2e8a61f1d1a767d59644460a9fa3`.
- `backend/tests/unit/test_snapback_promotion_scope.py` committed blob:
  `9ba861809c8ce267cf12d94cf3e3788f0f40af19`.

Both files were reconstructed in an isolated Python environment and each
matched its exact Git blob using `git hash-object`. `py_compile` passed;
`python -m pytest -q -p no:cacheprovider test_snapback_promotion_scope.py`
reported **16 passed, 0 failed**. This proves only the pure helper's local
behavior (current source and current test file).

Four additional service/session-ledger regression tests were committed to
the full repository. **Those tests have NOT been executed** against the
whole backend because the authenticated Sterling host/repo runtime is not
available in this session. No broker orders, accounts, new deployment, or
authoritative forward trade/economic receipts were accessed.

## Promotion disposition

**NO-GO.** At least 60 fully observed independent sessions and 300
authoritative completed trades *for one frozen strategy and build*, plus
required broker, drawdown, after-cost and operational proof, remain
unverified. Do not merge this unverified development branch into live
production or claim a profitable/live-certified release. The user is
receiving ICU treatment and should not be asked to run commands.

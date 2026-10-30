# Sterling V2 — Current controlled-review status (30 October 2026)

**Authoritative current checkpoint for PR #15.** The earlier
`2026-10-30-product-integrity-slice.md` is an implementation history: its
intermediate counts of five/eight legacy order sends are **superseded** by
this register. All source changes are review-only; the latest immutable SHA
must receive CI results before a test status can be recorded as PASS.

Repository: `nageshmadaram/sterling-v2`
Branch: `upgrade/product-integrity-hardening-20261030`
PR: #15 **DRAFT, OPEN, UNMERGED**. `main` unchanged.
Live order routing / live trading: **NOT AUTHORIZED**.
Production certification/deployment: **NOT ACHIEVED / NO-GO**.
External production controls: **0/12 accepted, 12/12 blocked**.
Independent acceptance: **NOT PROVIDED**.

## Completed in source, NOT independently accepted

| Area | Source result | Proof still required |
| --- | --- | --- |
| Canonical order sends | Five former direct strategy SELL calls and the previously undetected **SuperTrend monitor indirect broker-method alias** are now routed through canonical execution; AST direct-order allowance is **zero**, and the guard now detects broker-method rebindings. Generic Kite HTTP `c._place` remains explicitly **paper-only**, not canonical; no live fallback is permitted. | Latest-SHA AST/strategy suite PASS, actual legacy inventory recovery, and removal of remaining paper-only execution exceptions |
| Reduction authority | New common bridge requires exact account, known boolean broker mode, valid immutable signal, non-reconciled inventory refusal, exact exchange/symbol and sufficient durable quantity. It never fabricates an inventory row or creates a new exposure when a reduction cannot be proved. `CanonicalExecutionService` still owns broker send, bound capability, fresh broker inventory (two checks), journal and UNKNOWN fence. | Broker product-bucket, quantity and concurrent-gateway evidence; operational handling of legacy positions with no canonical inventory |
| Legacy recovery safety | Adaptive Edge and Intraday persist the pending exit before send, and do not re-arm/retry on ambiguous transport outcomes. Gamma uses the deterministic canonical order tag for lost-ACK broker-book recovery. Emergency Opening Volume SELL refuses unverified inventory and halts for protection failure. | Strategy-specific projection of broker fill/trade IDs back into canonical journal, crash gap coverage and operator no-double-SELL acceptance |
| Order outcome | Kite POST returns HTTP 409 for PENDING/UNKNOWN idempotency markers; mobile requires a non-sentinel broker order ID and never calls an ACK a fill. | Route regression and real device/browser errors |
| Access | Kite feature + role write checks for admin, operator, legacy user and viewer; broker config action authority for activate/test; simulation identity explicitly cannot mutate live Kite state. | Full HTTP route inventory, token revocation, workspace visibility and end-to-end device testing |
| SQLite | Actual canonical journal and strategy reduction tests exercise immutable replay, UNKNOWN and isolated SQLite backup/reopen. Existing SQLite tool checks integrity/schema/row-count parity and does not overwrite source. | Complete clean latest-SHA run and independent full-host restart |
| Mobile Expo | Expo 58 and React Native constraints updated with generated npm lock metadata; static manifest/lock matrix test and `npm ci`/Expo check in focused CI. | Real clean install, Expo doctor/install --check, native app boot, Android/iOS export and real-device flows |
| Rollback preflight | New read-only Alembic AST audit identifies destructive baseline downgrade operations; tests pin refusal to treat automatic downgrade as safe. | Actual disposable PostgreSQL upgrade, snapshot/restore, negative migration and failure cleanup tests |

## P0 acceptance blockers — DUE, unscheduled

1. **Canonical fill/position projection**: new canonical reduction fill
   observation and versioned `mark_reduction_projected` hooks cover Adaptive Edge,
   Intraday and Gamma exits; SuperTrend's journal consumer retains pending
   evidence if its fill ledger booking fails; Opening Volume keeps the emergency
   close projection pending. **Source implemented, latest-SHA tests and full
   broker-trade-ID, partial-fill, and crash/restart projection NOT CERTIFIED**.
2. **Legacy inventory migration**: reconcile old per-strategy positions with
   canonical inventory from authoritative broker data and immutable account,
   exchange, product and quantity. Never blindly insert canonical inventory
   from a strategy's local cache. Until reconciled, the new canonical reduction
   bridge **refuses** the SELL; treat any unprotected live position as requiring
   immediate supervised/manual broker attention, not an automatic retry.
3. **UNKNOWN reconciliation**: the same stable intent must remain unclaimable
   after ambiguous send or process death. Operator/broker-book evidence must
   resolve exact tags/order IDs. Confirm stop cancellation, partial fills,
   scale-out, remaining-quantity protection and daily-loss accounting.
4. **Global route policy completion**: exercise all write verbs/endpoint
   combinations, including broker assignment, workspace grant revocation,
   API/Telegram callbacks, admin/operator/viewer and simulation mode.
5. **Latest commit CI**: focused acceptance, full backend/CI, frontend,
   mobile, zero-regression and Snapback gates require completed results.
   Queued/cancelled jobs and historical PASS runs do **not** certify HEAD.

## P1 acceptance blockers — DUE, unscheduled

- PostgreSQL: run Alembic upgrade on a *disposable* instance with a
  dedicated sync Postgres driver and locked dependencies, real schema/data
  snapshots, restore comparison and negative/partial migration cleanup.
  **NOT EXECUTED**; a read-only downgrade AST audit is not a PG recovery drill.
- Browser/device: desktop Playwright, mobile Android/iOS navigation, initial
  session + refresh, role revocation, order ACK/UNKNOWN and Kite error journeys
  need real-device evidence. **NOT EXECUTED** in this review.
- Expo: validate `npm ci`, `npx expo install --check`, native launch,
  Android/iOS export and supported runtime. Exact package/lock alignment
  alone does not close issue #16.
- All pending reviews, artifacts and acceptance dates: **NOT SCHEDULED**.

## External / not worked / blocked

Production host provisioning, TLS/static egress, secrets custody,
administrator MFA on the actual operator device, broker/market connectivity,
real-market order-ack reconciliation, production database restore,
independent monitoring/notification delivery and independent release
approver receipts remain **BLOCKED / NOT ACCEPTED**. No step in this
review claims these have happened.

## Offline verification commands

```bash
# Branch review (read-only)
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git diff origin/main...HEAD --check

cd backend
python -m compileall -q app
python -m pytest -q tests/unit/test_canonical_execution_convergence.py \
  tests/unit/test_canonical_strategy_reduction.py \
  tests/unit/test_execution_unknown_settlement.py \
  tests/unit/test_canonical_journal_snapshot_restore.py \
  tests/test_workspace_access.py tests/test_role_based_write_access.py \
  tests/test_kite_router.py tests/test_order_router.py \
  tests/services/test_adaptive_edge_execution.py \
  tests/services/test_intraday_runner.py \
  tests/services/test_gamma_move_live_audit.py \
  tests/services/test_opening_volume_execution.py
cd ..
PYTHONPATH=. python -m pytest -q scripts/tests/test_sqlite_recovery_preflight.py \
  scripts/tests/test_alembic_rollback_preflight.py
python scripts/audit_alembic_rollback.py

cd mobile
npm ci --ignore-scripts --no-audit --no-fund
npm run ts:check
npm test
npx expo install --check
npx expo export --platform all

cd ../frontend
npm ci
npm run media:styles
npx tsc --noEmit
npx vitest run src/pages/__tests__/WorkspaceRouter.test.tsx --coverage=false
```

**These are commands for CI/reviewers; they are not claimed as completed
unless the exact-head execution output is attached.** Run the usual full
CI matrix before approval, not only this focused selection.

## Rollback and operational notes

- Keep draft PR #15 unmerged and services disconnected from live brokers.
  For a source regression, revert the reviewed branch commit(s) only,
  preserving Git ancestry and a full audit trail; do not force-reset `main`.
- **Never** auto-run `alembic downgrade`: the baseline downgrade drops
  trading-relevant ORM tables. The read-only preflight reports this as
  `BLOCKED_AUTOMATIC_DOWNGRADE`; a real PG restore requires approved
  backups and independent checks.
- **Never** delete or recreate execution journals, recovery flags, or
  broker account binding manifests merely to make a test pass.
- On UNKNOWN: retain the original intent key, broker tag and exit latch;
  reconcile broker orders/trades/positions first. Do not retry, re-arm
  a potentially duplicate SELL, or clear a kill switch without explicit
  operator investigation and approval.
- Do not activate a live broker, deploy, merge PRs or grant live trading
  authority under this review workstream.

**Final decision: REVIEW IN PROGRESS; PRODUCTION NO-GO.**


## Later implementation checkpoint — indirect exit bypass and projection integrity

**Implementation changes committed after the original five-call migration:**

- SuperTrend `kite_engine/monitor.py` had an indirect bypass:
  `place = client.place_order_future / place_order_option` followed by
  `await place(...)`. The prior AST guard only recognized direct attribute
  calls, so it missed this. The exit now creates a deterministic canonical
  `ke...` journal tag and uses `submit_reduction`, including short-cover
  BUY reduction for a verified negative holding. An AST regression rejects
  rebinding order methods to local aliases.
- `reduction_inventory_refusal` now verifies canonical inventory **before**
  disarming an existing GTT. This applies to SuperTrend, Gamma, Adaptive Edge,
  full Intraday exit, and Intraday scale-out. Unreconciled migrated holdings
  retain protection and refuse the automatic exit. This does NOT solve
  operator recovery for pre-existing unprotected positions.
- Canonical `resolve_canonical_reduction` attaches confirmed cumulative
  broker evidence to its exact order-intent journal. Adaptive/Intraday/Gamma
  record the projection version only after saving their strategy position
  and accounting state. Opening Volume leaves projection pending because
  its emergency close has no equivalent durable strategy-close update.
- The shared `execution_lifecycle` consumer no longer marks another
  strategy's canonical EXIT projected by assumption. SuperTrend likewise
  leaves projection pending when signed ledger booking fails or there is
  no attributable position.
- Gamma's canonical UNKNOWN order cannot be disproved by one empty
  broker-book read; it remains fenced for operator reconciliation rather
  than automatically re-arming an uncertain SELL stop.
- Auth now respects Sterling's original virtual Simulation contract:
  the designated owner may mutate its **isolated simulated book**, while
  live broker/global operator actions are refused. Regression coverage
  explicitly exercises both.
- The generic Kite HTTP raw order sender remains isolated to verified
  **paper** mode, with a second explicit guard immediately before `_place`.
  It is still a noncanonical paper order path and remains due for
  integration with canonical simulation journaling. This exception is not
  authority for a live order.
- Focused CI now includes the SuperTrend GTT pre-disarm and projection
  negative cases, cross-strategy journal ownership and Expo's multi-platform
  export check.

**Latest-head status remains UNVERIFIED until GitHub Actions completes.
** Source changes and test additions are not a PASS claim. Full real-device
navigation, PostgreSQL point-in-time restore, independent brokerage
reconciliation and release acceptance remain open or externally blocked.

**Operational rollback:** preserve SQLite WAL and every canonical intent,
outbox, exact broker tag, per-strategy position file and signed-fill ledger.
Never clear `projection_pending` or recovery flags as a cleanup shortcut.
Do not revert to a prior unsafe direct SELL sender in response to blocked
legacy inventory; require approved broker-account reconciliation and
review of the original pending order before any renewed action.


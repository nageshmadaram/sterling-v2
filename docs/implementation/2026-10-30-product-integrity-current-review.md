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
| Canonical order sends | BrokerAgent and OrderRouter's noncanonical sends retired. All five formerly grandfathered SELL paths (Adaptive Edge 1, Gamma 1, Intraday 2, Opening Volume 1) now invoke `canonical_strategy_reduction.submit_reduction` instead of `client.place_order*`. AST grandfathered allowance is **zero**. | Latest-SHA AST/strategy suite PASS, real inventory migration and restart test |
| Reduction authority | New common bridge requires exact account, known boolean broker mode, valid immutable signal, non-reconciled inventory refusal, exact exchange/symbol and sufficient durable quantity. It never fabricates an inventory row or creates a new exposure when a reduction cannot be proved. `CanonicalExecutionService` still owns broker send, bound capability, fresh broker inventory (two checks), journal and UNKNOWN fence. | Broker product-bucket, quantity and concurrent-gateway evidence; operational handling of legacy positions with no canonical inventory |
| Legacy recovery safety | Adaptive Edge and Intraday persist the pending exit before send, and do not re-arm/retry on ambiguous transport outcomes. Gamma uses the deterministic canonical order tag for lost-ACK broker-book recovery. Emergency Opening Volume SELL refuses unverified inventory and halts for protection failure. | Strategy-specific projection of broker fill/trade IDs back into canonical journal, crash gap coverage and operator no-double-SELL acceptance |
| Order outcome | Kite POST returns HTTP 409 for PENDING/UNKNOWN idempotency markers; mobile requires a non-sentinel broker order ID and never calls an ACK a fill. | Route regression and real device/browser errors |
| Access | Kite feature + role write checks for admin, operator, legacy user and viewer; broker config action authority for activate/test; simulation identity explicitly cannot mutate live Kite state. | Full HTTP route inventory, token revocation, workspace visibility and end-to-end device testing |
| SQLite | Actual canonical journal and strategy reduction tests exercise immutable replay, UNKNOWN and isolated SQLite backup/reopen. Existing SQLite tool checks integrity/schema/row-count parity and does not overwrite source. | Complete clean latest-SHA run and independent full-host restart |
| Mobile Expo | Expo 58 and React Native constraints updated with generated npm lock metadata; static manifest/lock matrix test and `npm ci`/Expo check in focused CI. | Real clean install, Expo doctor/install --check, native app boot, Android/iOS export and real-device flows |
| Rollback preflight | New read-only Alembic AST audit identifies destructive baseline downgrade operations; tests pin refusal to treat automatic downgrade as safe. | Actual disposable PostgreSQL upgrade, snapshot/restore, negative migration and failure cleanup tests |

## P0 acceptance blockers — DUE, unscheduled

1. **Canonical fill/position projection**: prove for each of the five migrated
   exits that broker trade-ID and cumulative fill evidence reaches the canonical
   journal, updates the correct strategy registry exactly once, and survives
   restart. Current order submission is journaled; full strategy projection is
   **NOT CERTIFIED**.
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

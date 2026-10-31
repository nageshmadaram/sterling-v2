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



### Latest source refinement: Gamma UNKNOWN recovery and journal ownership

- A Gamma-Unwind `ke...` exit tag that appears **exactly once** in the broker
  order book is now matched against the existing canonical journal account,
  contract, product, SELL side and requested quantity. Only then is a lost
  acknowledgement rebound to that intent. An ambiguous, conflicting or missing
  journal identity leaves the original exit fenced.
- A canonical Gamma exit with no broker-book match is **not** declared unsent
  merely because a single read returned an empty list. Operator reconciliation
  remains necessary; the old protective GTT is not silently rearmed.
- A shared broker-fill consumer never clears another strategy's EXIT
  `projection_pending` marker. SuperTrend likewise retains its marker when
  the signed fill ledger write fails. A strategy-specific confirmed fill
  may clear its exact version only after the strategy registry is durable.
- New negative tests cover the exact-tag Gamma acknowledgement recovery,
  absent-book UNKNOWN fencing, cross-strategy journal retention, SuperTrend
  ledger failure, signed short-cover reduction, and pre-disarm GTT preservation.

**Acceptance distinction:** source and regression tests have been committed,
but no latest-head CI PASS or full runtime recovery proof has been established.
The five previously grandfathered calls plus the discovered SuperTrend
indirect alias are source-migrated; isolated PAPER HTTP order handling still
requires later canonical integration. Existing live holdings without
authoritatively reconciled journal inventory require operator attention.


## OpenAlgo-pattern compatibility migration increment (30 Oct 2026)

The requested Sterling/OpenAlgo upgrade plan is implemented **only for its
safe first slice**. Full adoption and production certification are not done.

| Phase | Completed in source | Pending / blocked |
| --- | --- | --- |
| 0 — freeze/inventory | Explicit offline `upgrade/sterling_openalgo.py` read-only schema and table/count scan, source never written | Operator-controlled snapshot/secret custody and full state inventory not performed |
| 1 — compatibility | `2026-10-30-openalgo-compatibility-matrix.md` plus AST-only `upgrade/api_inventory.py` showing actual route deltas | Exact OpenAlgo physical model and all service/strategy/UI contract parity |
| 2 — migration | Disposable SQLite backup; WAL converted to independent file; required table AND typed column checks; transactionally inserted version ledger with baseline SHA; re-run idempotence, collision refusal, atomic 0600 publish, failure cleanup | ORM/operational DB unification, PostgreSQL migration/restore, real-data approval, destructive downgrade prohibition |
| 3 — session | Original Sterling session/account/role code preserved, no broker mutation | Expiry, reconnect, session leakage proof on controlled test credentials |
| 4 — API | JWT, account-scoped **read-only** `POST /api/v1/compat/openalgo/orderstatus`, canonical fill/UNKNOWN status adapter; viewer read contract tested | OpenAlgo-native apikey authorization not accepted; placeorder/cancelorder/orderbook wrappers not enabled |
| 5 — strategy/execution | Prior canonical execution constraints preserved | Real replay/fill projection and broker/session certification |
| 6 — UI | No UI change required for copy-only source tooling | Desktop/mobile browser journeys, state hydration, Expo device acceptance NOT WORKED |
| 7 — validation | Added schema migration, corruption, WAL/restore, interrupted write, source-only route inventory and HTTP policy regressions to focused CI | **Latest-head CI results pending**, full integration/realistic independent restore not established |
| External | Nothing represented as accepted | Host/TLS/monitoring/broker/independent approval: **0/12 accepted, 12/12 blocked** |

**Version semantics:** `sterling_upgrade_ledger` is a marker on an
**isolated cloned SQLite file**, not a claim that the production DB or Alembic
was migrated. The preflight checks selected critical field names and SQLite
types but it is not complete semantic table equivalence. It refuses
missing/malformed operational contracts, does not copy database row values
into reports, and never rewrites user credentials, broker orders, journal
states, strategy risk rules or active environment variables.

**Operational rule:** all snapshots contain private account/session data even
when inventories redact values. Use restricted disk/storage and delete copies
through the approved process. `python -m upgrade.sterling_openalgo --source
/path/to/disposable.db` is read-only by default. `--apply-to-copy` needs a
new destination in another existing directory. Rollback is to discard that
copy, **never** downgrade or overwrite the source. No broker/live execution.

**Code / tests / commands:** see `upgrade/README.md`,
`scripts/tests/test_openalgo_compat_upgrade.py`,
`scripts/tests/test_openalgo_route_inventory.py`,
`backend/tests/unit/test_openalgo_compat.py`, and
`backend/tests/test_workspace_access.py`. Complete the repository's focused
and full CI checks against the immutable PR head before treating this as
validated; historical PASS runs and queued checks do not establish a PASS.

**Next due, unscheduled:** full typed table/foreign-key/index compatibility,
actual disposable PostgreSQL up/down/restore and corruption drills, all
session and UI parity tests, reviewed broker adapters, complete API auth/error
contracts and independent release evidence. **NO-GO remains unchanged.**


## Continued migration checkpoint — disposable PostgreSQL + content integrity

- Copy-only SQLite backup now compares **ordered SHA-256 content fingerprints
  of all operational tables**, not merely table names/row counts. A test
  modifies a staged row without changing the count; the copy is refused,
  removed and the source remains unchanged.
- Alembic supports the pinned `postgresql+asyncpg` connection through the
  SQLAlchemy asynchronous migration engine, but allows PostgreSQL only for an
  explicitly marked, nonproduction `sterling_test_*` /
  `sterling_disposable_*` DB. A matching production environment refuses.
- Alembic now refuses the active raw Sterling SQLite DB, including a renamed
  operator-path alias; only isolated ORM/staging databases are eligible.
- Focused CI includes disposable SQLite Alembic repeatability and URL safety
  tests; a separate PostgreSQL 16 service workflow exercises repeated
  upgrade, synthetic transaction rollback, pg_dump and second-DB pg_restore.
  **These are committed test definitions, not an observed CI PASS**.
- Full operational schema migration from raw SQLite to PostgreSQL remains
  **P0 pending**, including journal, signed fills, account/sessions,
  execution controls and strategy registry reconciliation. The existing
  destructive ORM baseline downgrade is still disallowed as rollback.

**Current boundary:** no operator database altered, no broker accessed,
no deploy or merge; PR #15 remains draft. The new PostgreSQL workflow
must complete on the exact commit before its isolated ORM drill can
be called passed. Production 0/12 accepted, 12/12 blocked; **NO-GO**.


## 31 October 2026 — recovery and broker evidence validation increment

**Source-only implementation, pending exact-head CI acceptance.**

- The independent `scripts/verify_sqlite_recovery.py` snapshot verifier now
  compares all operational table contents with private, ordered SHA-256 digests,
  full `sqlite_master` definitions, row counts, `user_version`,
  `application_id`, SQLite integrity and foreign-key checks. A verified
  private (0600), standalone DELETE-journal snapshot is published atomically
  and without overwrite; a staging failure deletes only the unpublished copy.
  No data hashes, row values, broker tokens or private paths are returned.
- Offline recovery tests cover **same-row-count tampering**, source immutability,
  durable WAL records, BLOB and WITHOUT ROWID tables, symlink rejection,
  atomic create collision, file permissions and cleanup. These are
  committed test cases, **not observed green test results**.
- Canonical broker fill parsing refuses nonintegral quantities and nonfinite
  prices. A COMPLETE order without exact filled quantity cannot be assumed
  filled from its average price. Overfills, unsupported broker statuses and
  filled orders missing a positive confirmed execution price cannot settle.
- Multiple broker trade rows are aggregated only with a unique trade ID and
  compatible order identity. Duplicate IDs do not double-count, and missing,
  conflicting, cross-order or otherwise invalid trade rows cannot raise the
  independently reported cumulative fill. Order-history-only evidence
  remains possible, so cross-source agreement and broker-trade-ID durability
  are **not certified** by this increment.
- These safeguards do **not** implement full operational SQLite-to-PostgreSQL
  migration, restart-safe reconciliation of every legacy strategy position,
  or native mobile acceptance. No real broker, production DB or deployment
  was touched; no source branch merge is approved.
- The new dedicated SQLite tests are already in the focused offline CI
  workflow along with canonical strategy reduction tests. Completion on the
  exact resulting HEAD, not older SHA successes, remains mandatory.

**Acceptance unchanged:** PR #15 draft/unmerged; main unchanged; 0/12 external
production controls independently accepted; production deployment **NO-GO**;
live execution and broker routing **NOT AUTHORIZED**.


## 31 October 2026 — strict operational schema readiness gate

The uploaded upgrade specification's schema-first and rollback requirements
remain incomplete. In draft PR #15 the read-only
`--audit-full-parity` command now checks all discovered SQLite table
names, safety-relevant PRIMARY KEY/UNIQUE combinations, separately initialized
user tables and critical column declarations. It detects missing keys,
partial indexes that are not globally unique, and SQLite's nullable
`TEXT PRIMARY KEY`/nullable unique-key mismatch with PostgreSQL.
All results are metadata-only; private rows and credentials are not exported.

This deliberately exits **2 / BLOCKED_FULL_MIGRATION**, and cannot be
combined with a copy/write request. It does not introduce PostgreSQL
models, transfer operational rows, migrate authentication tokens, change
runtime DB flags, run a broker, or claim schema parity. Offline synthetic
tests cover redaction, repeatability, missing identity constraints,
partial versus unconditional uniqueness, nullable primary keys and
negative CLI status. **No exact-head CI PASS is claimed.**

Complete operational PostgreSQL schema/data migration, rollback, independent
restore, broker account/session recovery, every strategy's execution
convergence, full API compatibility and desktop/native acceptance remain P0/P1
work. Controls 0/12 accepted; deployment/live routing NO-GO.

# Sterling V2 -> OpenAlgo pattern: verified compatibility baseline

**Date:** 2026-10-30. **Source branch:** `upgrade/product-integrity-hardening-20261030`,
draft PR #15 (do not merge). This is a **pattern/reference migration**, not a
replacement of Sterling's risk policy, broker authority, or strategy semantics.

Reference architecture and API documentation:
- https://docs.openalgo.in/developers/design-documentation/database-layer
- https://docs.openalgo.in/installation-guidelines/getting-started/upgrade
- https://docs.openalgo.in/api-documentation/v1/orders-api/placeorder
- https://docs.openalgo.in/api-documentation/v1/orders-api/orderstatus
- https://docs.openalgo.in/api-documentation/v1/accounts-api/orderbook
- https://docs.openalgo.in/api-documentation/v1/orders-api/cancelorder

**Important evidence limit:** OpenAlgo's published database design is
conceptual; it does **not** prove literal one-to-one equivalence of Sterling
table columns, broker states, user model or native restore procedures. No
unreviewed OpenAlgo schema is imported.

## Domain compatibility matrix

| Domain | Sterling source | OpenAlgo pattern | Implemented delta | Pending / acceptance |
| --- | --- | --- | --- | --- |
| Runtime | `backend/main.py` FastAPI route domains | Python API + grouped services | Existing modular service layers retained | Complete bootstrap parity and staged runtime acceptance |
| Operational database | `backend/app/services/db.py` raw SQLite (orders, signed fills, account inventory, risk controls, positions, configs) | Domain-specific SQLAlchemy persistence | Read-only schema inventory and atomic copy-only version ledger | Full model/column mapping; PostgreSQL migration and restore |
| ORM | `backend/app/persistence/models.py`: `positions`, `equity_snapshots`, `mirrored_records`; one Alembic baseline | Versioned migrations | Destructive downgrade audit and clone-only additive ledger | Convert remaining operational tables; independently tested forward/rollback plan |
| Identity | `auth/user_store.py`, `account_policy.py`, JWT, role+workspace grants | Credentials/session/authentication | Preserve Sterling grants; no API-key trust substitution | Session refresh, revocation, multi-account failure injection |
| Broker sessions | `exchanges/kite/accounts.py`, Kite callbacks | Broker adapters with session lifecycle | No broker session mutation; account table inventoried | Expiry/reconnect and cross-account session isolation |
| Execution | `execution_service.py`, `order_journal.py`, signed fills, strategy registries | Single validated order path | Canonical strategy reductions retained from prior PR increment | Complete legacy paper-only path migration and full replay proof |
| Risk | `execution_control`, `kite_inventory`, shared safety supervisor | Central risk guard | Existing controls preserved | Risk semantic parity evidence; no unapproved policy change |
| Strategies | Snapback, Gamma, SuperTrend, Adaptive, Intraday and others | Registered strategies | Existing registry unmodified | Full strategy lifecycle/parity smoke tests |
| UI | Sterling desktop + Expo mobile | Stable API contracts | Read-only order-status compatibility response isolated | Browser/mobile/real-device journeys and API fallback parity |
| Deployment | Sterling scripts and runtime env | Config-based startup | No deployment scripts or env values mutated | TLS/host/secrets, monitoring and operator approval |

The reproducible static route delta command is `python -m upgrade.api_inventory`.
It parses FastAPI decorators without importing the application or loading broker
credentials. A declared route is **not** runtime/UI acceptance evidence.

## Explicit API route delta

| OpenAlgo documented route | Existing Sterling route/pattern | Status |
| --- | --- | --- |
| `POST /api/v1/placeorder` | `POST /api/v1/kite/orders` plus canonical execution | **BLOCKED**: request auth, broker/risk semantics and response differ; no writer wrapper |
| `POST /api/v1/orderstatus` | `GET /api/v1/kite/orders/{order_id}/history` | **PARTIAL**: `POST /api/v1/compat/openalgo/orderstatus` is JWT-protected, scoped, journal-only read response; not drop-in OpenAlgo API |
| `POST /api/v1/orderbook` | `GET /api/v1/kite/orders` | **PENDING**: broker fetch, authentication and data contracts differ |
| `POST /api/v1/cancelorder` | `DELETE /api/v1/kite/orders/{order_id}` | **BLOCKED**: live cancellation has separate exact-intent authority; no compatibility write route |

Do not silently normalize `UNKNOWN` to `complete`, return `PENDING` as an
`orderid`, or accept OpenAlgo `apikey` as Sterling identity. The mounted
adapter checks the JWT + Kite feature grant + authenticated user/account
ownership. Viewers may read but cannot mutate execution.

## Database schema delta and versioning

The read-only scanner checks **known Sterling tables**, not guessed OpenAlgo
physical model names:

- `kite_accounts`, `users` (users created by separate user-store init)
- `kite_order_intents`, `kite_order_observations`, `kite_fill_ledger`,
  `kite_execution_increments`
- `positions`, `kite_inventory`, `execution_control`, `system_config`

The clone-only ledger holds `version`, `phase`, and a SHA-256 signature
of the original operational schema. It is **not** a global deployment
version or a substitute for the `alembic_version` ORM ledger. No live table
is dropped, copied into a guessed OpenAlgo model, or rewritten.

## Configuration and startup delta

| Sterling | OpenAlgo documented pattern | Decision |
| --- | --- | --- |
| `STERLING_DB_PATH` operational DB and `DATABASE_URL` ORM mirror | `DATABASE_URL`, modular databases | Keep separate until reconciled; do not repoint blindly |
| Sterling runtime environment loading in `backend/main.py` | .env / startup env version | No changes to operator .env or secrets |
| User/account scoped JWT + session metadata | OpenAlgo apikey and broker auth | Explicit compatibility adapter; do not substitute credentials |
| Sterling `scripts/sterling-start` / supervised stack | OpenAlgo app bootstrap | No service rewrite or deployment |
| Canonical execution gates, inventory and kill switches | OpenAlgo risk/execution layers | Sterling gates remain stricter and authoritative |

## Phase register — completed / due / blocked

| Phase | Implementation | Current status | Due |
| --- | --- | --- | --- |
| 0 Freeze/inventory | Copy-only tooling, metadata-only audit, no live writes | Implemented in source, CI pending | Unscheduled acceptance |
| 1 Compatibility baseline | This schema, API and config delta inventory | Source implemented; detailed column parity pending | Unscheduled |
| 2 Migration layer | Atomic isolated SQLite copy + idempotent, fingerprinted version ledger | **Subset implemented**, no live/PG migration | Unscheduled |
| 3 Broker/session | No changes | **PENDING / external broker proof blocked** | Unknown |
| 4 API | JWT-scoped read-only status compatibility endpoint | **Partial**; order writer/cancel blocked | Unscheduled |
| 5 Strategies | Prior canonical migration retained | Full restart/reconciliation unproven | Unscheduled |
| 6 UI | No UI changes in this increment | **NOT WORKED** desktop/mobile E2E | Unscheduled |
| 7 Validation | New SQLite clone/recovery, API auth, journal contract regressions | Tests committed; latest-head CI result required | Unscheduled |
| External | Production host, broker orders, independent release receipt | **0/12 accepted, 12/12 blocked** | Unknown |

## Operations and rollback

Never run schema transformations against the operator's live database in this
review. Use only explicitly chosen isolated SQLite inputs/destinations.
`--apply-to-copy` refuses existing destinations, same-directory copies,
missing critical tables and altered ledger fingerprints; it checks WAL,
schema, row counts, foreign keys and integrity and cleans interrupted stages.
The copy contains secrets even though the report does not: mode 0600,
restricted storage, no publishing artifacts. Rollback is discard of the copy,
**not Alembic downgrade**. The baseline downgrade drops ORM tables. A
PostgreSQL restore must be independently witnessed before any runtime switch.

Validation commands in `upgrade/README.md`; additional full CI, browser,
Expo native and broker-provider proofs remain required.

**Certification boundary: OFFLINE REVIEW ONLY; PRODUCTION NO-GO; LIVE
TRADING AND BROKER ORDER ROUTING NOT AUTHORIZED.**


## Follow-on migration hardening: data parity and disposable PostgreSQL drill

The copy-only SQLite upgrade now compares per-table **content fingerprints**,
not only schema definitions and row counts. Digests are used internally and
are not emitted in the public inventory; staged data corruption with an
unchanged row count causes cleanup and refuses publish. Snapshot comparison
still does not prove the entire host, secrets custody, or PostgreSQL readiness.

The Alembic migration environment now uses the already pinned **asyncpg**
driver through SQLAlchemy's asynchronous engine. It refuses PostgreSQL
targets unless all of these hold:

- Database name begins `sterling_test_` or `sterling_disposable_`
- `STERLING_ALEMBIC_DISPOSABLE=1` is explicitly present
- Effective Sterling runtime environment is **not** production
- PostgreSQL driver is `asyncpg`; unsupported / unpinned drivers fail closed

These gates make misuse harder but are not proof of the target's actual
disposability, nor are they external release approvals.

Added `.github/workflows/postgres-migration-drill.yml`: a
**disposable PostgreSQL 16** service job that performs a repeated Alembic
upgrade, inserts synthetic ORM data, rolls back a synthetic transaction,
takes a custom-format database dump and restores into a distinct test DB,
then verifies Alembic revision, retained data and absent rolled-back data.
It does **not** operate on Sterling's raw operational SQLite or use real
broker credentials. CI is evidence only **if the exact commit's workflow
actually completes successfully**. Queued jobs are not a PASS.

The scope still does not include PostgreSQL migration of all Sterling
execution journal, risk, broker-account, inventory or credential tables.
No live database switches or broker calls have been performed.


## Full migration preflight: new negative acceptance gate

The copy-only inventory is intentionally permissive enough to support
synthetic legacy SQLite snapshots. It was not a full operational migration
readiness result. The new
`python -m upgrade.sterling_openalgo --source <isolated.db> --audit-full-parity`
reports strict **account, authentication, order, signed-fill, inventory, lease,
execution control and settings unique-key gaps** and all discovered SQLite
table names without disclosing record contents or credentials. It treats a
partial unique index as insufficient to enforce immutable order identity.
This is a source-only addition on draft PR #15 with negative synthetic
regression coverage.

Its response is always `BLOCKED_FULL_MIGRATION` (shell exit 2) until
approved, complete PostgreSQL domain models, data transfer, rollback and
independent acceptance are delivered. The current raw operational schema has
no such verified PostgreSQL parity. Read-only auditing is not a substitute for
auth, broker order lifecycle or native user-journey acceptance.


### 31 October: auth and financial-evidence schema coverage expansion

The full-parity preflight now checks **20 explicit Sterling safety-key table
contracts**, based on source SQLite DDL rather than claimed OpenAlgo identity:
the original order/inventory/risk core plus platform administrator
designation, feature and workspace grants, policy revisions, auth events,
auth outbox, schema version and realized-P&L event identities.

It also reports (aggregate metadata only):
- `orphan_identity_row_counts`: references to missing user rows in the
  known account/authorization tables, without exposing IDs or secret rows.
- `missing_policy_guard_triggers`: missing source SQL trigger names that
  protect the designated administrator (name-presence only).
- `missing_declared_identity_foreign_keys`: missing actual SQLite
  references on `platform_admin` and `user_workspace_visibility`.
- `unmapped_operational_tables`: only tables *outside* the audited critical
  contracts, not the critical tables themselves.
- critical column-affinity, uniqueness and NULL-identity gaps for the newly
  covered tables and execution observation/increment/lease/conflict ledgers.

All reports remain **READ ONLY** and **BLOCKED_FULL_MIGRATION**. The guard
checks are not physical PostgreSQL DDL, credential transfer, target policy
enforcement, canonical replay, rollback certification or deployment.
Synthetic test source is added, but full exact-commit CI acceptance is pending.

# Sterling -> OpenAlgo-pattern upgrade (controlled review only)

This directory implements the **offline, additive phase-0/1/2 preparation**
defined in the Sterling OpenAlgo Upgrade Implementation Spec. It is **not**
OpenAlgo source code or an exact-schema import. Sterling remains FastAPI,
and all account grants, execution journal, broker safety and strategy logic
remain authoritative. Never interpret a successful dry-run as trading approval.

## Modes

`python -m upgrade.sterling_openalgo --source /path/to/disposable.db`

Default: **READ-ONLY**. Reports only schema fingerprint, table names/counts,
version and domain gaps (no row values, credentials or decrypted sessions).
An incomplete critical schema reports `BLOCKED_INCOMPLETE_SCHEMA`.

`python -m upgrade.sterling_openalgo --source /path/to/disposable.db --destination /another/existing/directory/staged.db --apply-to-copy`

Requires an explicit new destination outside the source directory.
The tool makes a consistent SQLite backup, checks integrity and foreign keys,
converts any WAL-mode snapshot to a standalone file, transactionally writes
one `sterling_upgrade_ledger` version row, verifies original table counts and
schema definitions, then atomically publishes a **0600** copy. An already
migrated copy is idempotent if and only if its ledger hash still matches the
operational schema. Unknown version/changed schema/corruption is a hard stop.
Temporary files are removed on any failed migration.

**Never** supply the operator's real database for `--apply-to-copy` without
an approved offline copy/snapshot workflow and secret custody plan. The tool
has **no** in-place migration mode, no restore-over-source mode, and no
automatic rollback/downgrade. The staged copy itself may contain sensitive
records: keep it encrypted, private, and delete it through approved handling.

## Scope and hard boundaries

| Category | Current behavior |
| --- | --- |
| Raw SQLite | Core operational tables inventoried; no source writes |
| Version ledger | Additive only on disposable clone, copy-only marker |
| Auth/broker sessions | Metadata table presence checked; no credentials exported |
| Order lifecycle | Existing canonical journal rows copied unchanged |
| PostgreSQL/Alembic | **NOT MIGRATED**; existing Alembic downgrade is destructive |
| OpenAlgo API | One Sterling-authenticated read-only adapter; no live writer compatibility |
| Strategy/risk/UI | Original behavior unchanged; end-to-end parity unproven |
| Broker/live trading | **NOT ATTEMPTED / NOT AUTHORIZED** |
| Production deployment | **NO-GO**, controls accepted 0/12 |

The OpenAlgo-style response `POST /api/v1/compat/openalgo/orderstatus`
requires a Sterling **Bearer JWT** and JSON `{"account_id":"...","orderid":"..."}`.
The route is read-only and returns canonical journal state for the principal's
account. It does not accept OpenAlgo `apikey`, fetch live broker status, or
offer `/api/v1/placeorder`. `pending_confirmation` is not a fill. Other
API wrappers remain pending until identity and broker event parity are proven.

## Validation commands (offline reviewer/CI, not evidence of execution)

```bash
PYTHONPATH=. python -m pytest -q scripts/tests/test_openalgo_compat_upgrade.py \
  scripts/tests/test_openalgo_route_inventory.py
python -m upgrade.api_inventory
cd backend
python -m pytest -q tests/unit/test_openalgo_compat.py \
  tests/test_workspace_access.py
cd ..
python scripts/audit_alembic_rollback.py
```

Review full CI run on the **exact PR commit**, not earlier SHA results.
Never run tests against an operator database. Each pytest fixture uses a
private temporary SQLite file.

## Recovery and rollback

1. Preserve the original state, its WAL/SHM sidecars as applicable,
   account-binding manifest, and canonical journal evidence. Never replace
   the source with a migration artifact or clear UNKNOWN intents.
2. On failure, inspect the JSON error/fixture logs; no destination is
   published and the incomplete stage is deleted. Do **not** auto-retry a
   trade or session update.
3. For a successful disposable copy, rollback means discarding that copy.
   **Do not** `alembic downgrade`: the current ORM baseline drops core tables.
4. Any future PostgreSQL migration requires schema/model parity, independent
   database backup/restore, failure injection, migration checksums and
   operator/independent sign-off. Until then all runtime DB flags remain as
   they were and no live broker connection is permitted.

See `docs/implementation/2026-10-30-openalgo-compatibility-matrix.md` and
`docs/implementation/2026-10-30-product-integrity-current-review.md`.


## Disposable PostgreSQL drill (CI only)

`.github/workflows/postgres-migration-drill.yml` provisions a synthetic
PostgreSQL 16 service. It runs the pinned `asyncpg` Alembic migration twice,
proves rollback of a synthetic write, dumps the ORM database with `pg_dump`,
restores a **second disposable database** with `pg_restore`, and checks the
same durable synthetic record, ORM tables and Alembic revision in both.
Review the **exact SHA** workflow result; no result is claimed in advance.
No operator DB, broker credential, order, trade or live-control state is used.

The Alembic guard refuses the operational Sterling SQLite database and
refuses PostgreSQL unless its name starts `sterling_test_` or
`sterling_disposable_`, `STERLING_ALEMBIC_DISPOSABLE=1`, the pinned
`postgresql+asyncpg://` scheme, and an effective nonproduction runtime.
These checks are safety preconditions, **not** independent approval to point
Alembic at an externally hosted database.

The ORM migration remains only `positions`, `mirrored_records` and
`equity_snapshots`. Full Sterling journal, inventory, broker, auth, strategy
and risk table migration **has not been implemented or run on PostgreSQL**.


## Full operational migration preflight (read-only, deliberately NO-GO)

`python -m upgrade.sterling_openalgo --source /path/to/offline-disposable-snapshot.db --audit-full-parity`

This new **read-only** report is distinct from `--apply-to-copy`. It inventories
all table names without dumping any row data, detects missing core account,
order, signed-fill, inventory, risk, execution lease and identity tables,
checks critical column affinities, and checks **global primary/unique keys**
that enforce immutable execution identity. Partial or expression unique indexes
cannot substitute for those keys. It reports missing safety keys, current
schema fingerprint, unmapped operational table names and unavailable full
PostgreSQL migration/restore evidence. Do not point it at live broker storage;
use an independently preserved, custody-reviewed offline snapshot.

The full-parity audit now also covers **account-policy authority and
immutable financial evidence**, using Sterling's actual
`auth/user_store.py`, `auth/account_policy.py`, and
`services/db.py` source DDL. Its critical-key inventory includes the
singleton administrator designation, account-policy revisions,
`(user_id, feature)` grants and visibility keys, auth-audit IDs,
notification-outbox IDs, auth-schema versions, and
`(uid, event_key)` realized-P&L idempotency. It validates mandatory
column affinities for these as well as the order observation, execution
increment, lease, and conflict ledgers. Missing tables or incompatible
keys remain **migration blockers**, not instructions to manufacture
replacement user/position rows.

The `orphan_identity_row_counts` field reports **only aggregate counts**
of user references with no matching `users.id` across the known session
and account-policy tables. Legacy `default` principals and
simulation identities are never silently remapped or created. This
does not decrypt, display, or repair credentials. A counted orphan is a
plan-and-approve blocker for independent identity reconciliation.
`unmapped_operational_tables` now means tables genuinely outside
the reviewed safety-key inventory; it no longer repeats already mapped
critical table names. Additional strategy and feature tables still
require their own verified schemas and migration contracts.

The command returns **exit 2** and `BLOCKED_FULL_MIGRATION` even if every
SQLite key exists: the complete PostgreSQL operational schema, row-transfer
mapping, secrets custody, strategy registry parity, broker-state recovery,
independent restore and sign-off do not exist. An exact-key match is *not*
authorization to run SQL migrations or enable live routing. A full-parity
audit cannot be combined with a copy/write operation. The existing
`--apply-to-copy` and default dry-run contracts remain unchanged.

This gate implements part of the specification's phase-0/1 inventory and
phase-2 negative preflight; it does **not** deliver full schema migration,
reversible deployment or OpenAlgo physical model equivalence. Offline tests
live in `scripts/tests/test_openalgo_compat_upgrade.py` and are part of the
focused product-integrity workflow. Historical successful runs do not
validate a newer commit.

Never overwrite existing `.env`/secret material while adapting an OpenAlgo
runtime bootstrap. Merge new variable definitions selectively and require
operator review of any key rotation; the current project does not implement
credential migration.


**Important SQLite/PostgreSQL mismatch:** the report includes
`nullable_identity_columns`. In ordinary SQLite rowid tables, a
`TEXT PRIMARY KEY` declaration without `NOT NULL` may store multiple
`NULL` values. PostgreSQL primary keys cannot. The audit distinguishes
those columns from true `INTEGER PRIMARY KEY` rowid aliases and
`WITHOUT ROWID` primary keys. A globally unique index on a nullable
column is therefore not sufficient migration proof. This is reported
as a blocker requiring a reviewed data-cleanup and target-constraint
mapping, never silently repaired in the source.

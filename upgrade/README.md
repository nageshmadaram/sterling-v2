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

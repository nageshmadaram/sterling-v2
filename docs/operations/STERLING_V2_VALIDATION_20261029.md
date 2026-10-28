# Sterling V2 — 2026-10-29 validation register

## Scope and immutable evidence

This register covers **targeted** Phase 1 foundation, registry hardening, and Phase 2A *read-only* Kite normalization. It is not a claim of full-repository regression, production deployment, live Kite connectivity or go-live certification.

### Repository test evidence

| Check | Observed result | Evidence |
|---|---|---|
| Full-checkout SQLite foundation + registry safety | **PASS: 12 tests** | GitHub Actions run [40784019259](https://github.com/nageshmadaram/sterling-v2/actions/runs/40784019259) |
| Disposable PostgreSQL Alembic migration | **PASS** | Same run, PostgreSQL job |
| PostgreSQL immutability, audit rollback and one-way lock | **PASS** | Same run, PostgreSQL job |
| PostgreSQL 16 pg_dump/pg_restore into isolated DB | **PASS** | Same run, PostgreSQL job |
| PostgreSQL downgrade to `4d2c62ab8b01`, re-upgrade | **PASS** | Same run, PostgreSQL job |
| Broker read normalization + broker-registry + foundation suites | **PASS: 25 tests** | GitHub Actions run [40784609184](https://github.com/nageshmadaram/sterling-v2/actions/runs/40784609184) |
| No broker-write or API-route methods on normalized facade | **PASS** | Same run, AST/static interface check |
| Actual account-authorized Kite session or device acceptance | **NOT VERIFIED** | Requires operator/broker device proof |
| Full-repository CI / release suite | **BLOCKED** | Existing failures; this targeted job does not waive them |
| Privileged database DDL/INSERT threat and RBAC provisioning | **NOT RESOLVED** | Requires restricted DB credentials/role privileges and review |
| Full production backup/restore | **NOT VERIFIED** | Disposable CI DB is not a production restore exercise |

## Failure/fix history (evidence-based)

1. First PostgreSQL validation script failed before migrations due to the `backend/scripts` import path; changed invocation to `PYTHONPATH=. python scripts/validate_registry_postgres.py`.
2. PostgreSQL migration discovered malformed dollar-quoted PL/pgSQL function; fixed the trigger definition. SQLite never exercised this branch.
3. The restore rehearsal initially used the runner's newer PostgreSQL client, which emitted `SET transaction_timeout` unsupported by the PostgreSQL 16 server. Matched the client to server major version with `postgres:16` tools. The corrected restore test passed.
4. Broker normalization test initially compared a `Decimal` to `pytest.approx(float)` and raised TypeError. Changed to exact decimal comparison. The corrected suite passed 25 tests.

## Safety and next phase gates

- Changes are confined to additive registry SQLAlchemy models/migrations, internal registry write service, read-only broker observation models/facade, tests and docs. Neither execution service nor production trading flag was changed.
- Existing `CanonicalExecutionService` remains the only intended broker execution authority; new `NormalizedKiteReader` has **no** order-writing operations, risk approval or route registration.
- Account health and data availability are **not** live trading permission. Missing quote timestamps are unknown/stale; simulated broker health is not real authenticated connectivity.
- PRs remain draft/dependent. **No automatic merge, live enable or production deployment.**
- Before Phase 2B / Phase 3: validate signed-token integration, data-quality/fallback precedence, raw Kite access audit, tenant isolation, security review, full CI and real-device market feed evidence.
- Before any live activation: explicitly approved risk policy, actual broker identity and static outbound IP, reconciliation of unknown orders, fill/protection drills, immutable release and restore evidence.

The repository is not production-certified merely because targeted tests pass.

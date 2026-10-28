# Sterling V2 — Registry write safety (Phase 1 hardening)

**Status:** additive code on a dependent review branch; not an approved production release.

This slice extends the existing offline-only registry. OpenAlgo is only an architectural reference. No Sterling trading route, strategy scheduler, Kite broker integration, AI capability, or live execution flag is changed.

## Storage and write authority

- Alembic revision `ac19a63cb207` follows `4d2c62ab8b01` on the existing SQLAlchemy store (separate from live raw SQLite).
- New `platform_strategy_audit`: UUID, actor ID and role, owner, action, resource, nonempty reason, before/after SHA-256, and UTC epoch-millisecond timestamp; indexed by resource and actor.
- Database triggers on **SQLite and PostgreSQL** forbid UPDATE and DELETE of strategy version and audit rows, and prohibit hard DELETE of strategy records. This also protects against accidental ORM mutation outside the registry service. Registry deletion is a **soft retirement** of a disabled strategy.
- `platform_strategies.is_locked` blocks further metadata edits, version publication and offline run creation through the service. Lock is intentionally one-way in this slice.
- `StrategyRegistryService` accepts only a **signed Sterling access JWT** verified by the existing token + user-store check. It does not use the development-only `X-User-Id` fallback. Only verified admin/operator roles pass; the existing account store provisions admin/user only, so operator provisioning is **still a separate RBAC requirement**.
- The authenticated actor is derived server-side, never trusted from a UI payload. New strategy ownership is bound to authenticated actor; operator writes are owner-scoped.
- Every successful `create_strategy`, `update_strategy`, `lock_strategy`, `retire_strategy`, `publish_version` and `create_offline_run` command adds its audit row **in the same SQL transaction**. Failure to flush/commit the audit rolls back the mutation.
- Version publication uses canonical JSON + SHA-256; version reads verify canonical payload and digest. Duplicate strategy/version keys are rejected by a unique index. New runs accept `validation` or `simulation` only; no order submission path exists here.

## Limitations and security controls still required

This service is **not exposed as an HTTP API**. It has no operator UI, no broker access and no live-order admission. Direct privileged SQL can still forge **INSERT** rows/audits, bypass hash verification, or change mutable strategy metadata. PostgreSQL privilege separation, a complete transaction-bound event outbox and recovery testing are needed before any API rollout. Triggers prevent mutation/deletion, not privileged DDL or database-superuser tampering. SHA-256 is an integrity fingerprint, **not a digital signature**.

The existing legacy raw-SQLite stores and their actor/access-control mechanisms remain separate. This slice does not certify their audit completeness. Existing runtime strategy records are not automatically backfilled. Security review must also validate CSRF, anti-replay and owner binding at any future HTTP boundary.

## Validation

Run with disposable DBs only:

```bash
cd backend
pytest -q tests/test_platform_strategy_registry.py tests/test_platform_strategy_registry_safety.py
```

The tests cover SQLite migration, version integrity, duplicate rejection, write auth, lock, append-only triggers, and atomic audit failure rollback. A PostgreSQL staging migration and privilege/recovery drill are **not** covered and remain merge blockers.

**Rollback warning:** downgrading `ac19a63cb207` drops audit history and lock state. Do not downgrade a populated database without explicit restore-tested backup, operator approval and preservation of audit records. Continue neither to Phase 2 nor to merging/deployment until the actual repository checks, migration tests, risk review and rollback evidence are complete.

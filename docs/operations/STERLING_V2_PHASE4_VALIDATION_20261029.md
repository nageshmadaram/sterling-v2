# Sterling V2 Phase 4 — OFFLINE execution validation evidence

**Date:** 2026-10-29  
**Disposition:** targeted validation **PASS**; production activation **BLOCKED**.  
**Draft PR:** https://github.com/nageshmadaram/sterling-v2/pull/6  
**Verified implementation SHA:** `7be73b5f1439a0342c4501ddfaff4d645a8347d6`  
**GitHub Actions:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40793590277

## Repository-executed validation

| Check | Observed result |
|---|---|
| Offline state transitions, role policy, dry-run allow/defer/reject | PASS |
| Live input rejected regardless of state | PASS |
| Manual/semi-auto one-time input-bound operator approval | PASS |
| Paper auto-admission refused | PASS |
| Strategy identity/version integrity/run matching and owner isolation | PASS |
| Stale/degraded data, strategy failure and risk caps | PASS |
| Strategy-specific and global emergency stops | PASS |
| Emergency reset restricted to verified admin returning to validation | PASS |
| Pending emergency alert view with owner/admin access | PASS |
| Atomic signal/gate/risk/approval decision audit entries | PASS |
| SQLite direct UPDATE/DELETE immutability + forced audit rollback | PASS |
| Existing Phase 1–3 registry/broker/market-data regression suites | PASS |
| **Combined targeted test count** | **84 passed, 7 warnings** |
| Source access audit | PASS, **0 new unreviewed**, 272 inherited findings |
| No new order-writing methods or API route/broker transport imports | PASS |
| PostgreSQL 16 additive Alembic migration and audit immutability triggers | PASS |
| PostgreSQL illegal transition veto and audit-insert rollback | PASS |
| Disposable PostgreSQL 16 pg_dump / isolated pg_restore | PASS |
| Disposable Alembic downgrade to ac19a63cb207 and re-upgrade | PASS |
| Full repository regression and production broker acceptance | **NOT CERTIFIED** |

The seven warnings are from the pre-existing Phase 2B Python AST audit Markdown escape literals, not failures in the Phase 4 contracts. They have not been silently waived as a production gate.

## Changes

- New `app.domain.execution_validation` Pydantic contracts and fail-closed transition/evaluation state machine: disabled, validation_required, approved, blocked, paused and emergency_stop; auto, semi_auto, manual and blocked approvals; simulation, paper and live modes.
- New `app.persistence.execution_validation_service.OfflineExecutionGate` checks previously registered strategy/version/run identity using the existing registry service and signed actor policy, evaluates synthetic risk/health/market observations, and emits four audit events per dry-run.
- New `platform_gate_events` SQLAlchemy table and Alembic revision `b64f3c27e190`, with immutable UPDATE/DELETE triggers and chain-linked append-only events. System/strategy control is event-sourced, not a mutable approval flag. PostgreSQL advisory locking serializes gate transitions/evaluations against system emergency stops. This migration touches **no current Kite, execution or strategy runtime table**.
- Offline approvals bind to exact serialized gate input and expire after five minutes; they are consumed after an allowed dry-run. A dry-run `decision=allow` **never carries broker order authority**.
- Emergency stops set `alert_required=true, alert_delivery=pending`; authenticated read-only `pending_alerts()` exposes outstanding events, but does not send Telegram messages or claim receipts.
- No FastAPI routes, execution router changes, frontend controls, broker order callbacks, live flag changes, new execution authority, repository merge or deployment.

## Material constraints and pending exceptions

1. **Non-authoritative by design:** Current Sterling order paths were not rewired to this service. It therefore does **not** prove that existing legacy calls cannot bypass a future decision record. Deployment of a mandatory in-line gate requires a separate, controlled risk/execution integration change and its own live safety certification.
2. **Synthetic risk evidence:** The `source_verified` flag and exposure/market/strategy health values are inputs to an *offline* replay, not independently attested broker readings. A caller could supply fabricated offline values; even a resulting allow remains non-executing. Future trusted read interfaces, provenance signatures and live exposure/reconciliation proof are required.
3. **Alerts:** The append-only alert flag is not a sent/delivered message. Durable outbox, delivery retries, authenticated Telegram receipts and operator acknowledgement remain blocked pending implementation.
4. **Emergency stop:** The offline gate's stop does not currently operate the pre-existing Sterling runtime kill switches. The existing live safety controls remain unchanged. Administrative reset is audited but requires real incident evidence policy and dual control before any live integration.
5. **Database role controls:** PostgreSQL privileged INSERT/DDL can fabricate ledger rows or remove triggers. Application-role least privilege and an independent immutable audit/archive remain unverified. SQLite validation is disposable; PostgreSQL is required for transaction-serialized deployed gate operations.
6. **Legacy access inventory:** Phase 2B still records 30 direct broker-write call sites, 4 execution-authority call sites, 135 legacy reads, 25 API reads and 6 unknown/review cases (among other categories). Zero *new* findings is not closure of existing risks.
7. **Other certification:** Full backend/frontend/E2E and Snapback release gates, production restore, operator-mode provisioning, static-IP/broker identity, disconnect drills, moving-market fill/stop/reconciliation receipts and real-device monitoring are not certified.

**Decision: keep PRs #1–#6 as drafts. No merge, deployment, live orders, broker authorization, or Phase 5.**

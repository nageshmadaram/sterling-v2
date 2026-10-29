# Sterling V2 Phase 6 — Offline execution journal and reconciliation

**Scope:** validation-only evidence. No live trading authority, order routing,
broker writes, worker, main-branch merge, deployment or UI clone. This is
dependent on draft Phase 5 PR #7 and all earlier draft safety slices.

## Append-only atomic decision journal

Phase 6 adds Alembic revision `d86e31a4f290`, a separate
`platform_decision_journal` table with immutable UPDATE/DELETE triggers
(SQLite disposable tests and PostgreSQL 16). It stores: strategy/version/run
FK, signal batch, queue item and event IDs, UTC-millisecond timestamp,
actor/role, reason, risk state, approval mode/status, source quality summary,
queue/run state, approval/risk event references, immutable version/risk-policy/
lineage fingerprints, prior-record digest and current SHA256 digest, and
`execution_authorized=false` enforced both in application code and by a
database CHECK constraint.

`OfflineScheduler._append()` inserts its **original orchestration event**
and the **matching decision-journal event in the same SQLAlchemy transaction**.
The second insert is flushed; failure rolls back both. Each source event maps
one-to-one to one journal event, enforced by UNIQUE orchestration-event ID and
per-stream sequence. Journal reads verify source and journal chains, length,
sequence and matching event IDs; incomplete evidence is refused. A corrupted
ledger is never interpreted as safe-to-execute.

Phase 4's **separate** dry-run risk decision remains an independently
committed event stream. Phase 6 binds its risk/approval IDs and quality
summary to the Phase 5 journal, but does not claim a single transaction across
Phase 4 + Phase 5. On Phase 5 failure an unused Phase 4 *offline-only* decision
may remain; no broker access follows it. Production cross-stream atomicity
and distributed crash recovery remain out of scope and unverified.

## Reconciliation and lineage

`OfflineReconciler.inspect()` verifies both immutable event chains and
reconstructs each queue's canonical state. Reconciled outcomes include
queued, rejected, paused, cancelled, expired, failed, completed and
emergency_stopped. A stale non-terminal item is reported as degraded, not
automatically converted into a successful approval. Missing ledger rows,
invalid event ordering, missing provenance or orphan references cause
`untrusted` and a generic `AUDIT_OR_LINEAGE_INVALID` issue, never a live
execution decision.

An authenticated operator can explicitly invoke `mark_stale()` for an
already-queued item that exceeds its bounded age. It atomically appends
a `skipped/paused` transition, its journal record and a pending stale-item
alert. This cannot requeue, submit or execute any broker order. Requeue policy
remains in the Phase 5 risk-bound dry-run service.

Each journal record links a full strategy/version/run, batch, risk-policy
fingerprint, risk decision and operator approval record where available.
Unknown health/quality remains `unknown` or `offline_unverified` rather
than being silently upgraded.

## Operator/admin read-only surface

Two **GET-only** endpoints are registered, with signed Sterling JWT bearer
authentication enforced independently of the development anonymous identity:

- `GET /api/v1/offline-validation/strategies/{strategy_id}/decisions?limit=100`
- `GET /api/v1/offline-validation/strategies/{strategy_id}/reconciliation?limit=100`

Both are strictly owner/admin scoped through the existing registry identity
check; users cannot supply a different owner. Pagination is bounded.
Responses are intended for Sterling's operator/admin display and automatically
documented in API schema, but no frontend widget, command button or new
execution action is included in this validation slice. A real-device UI
acceptance receipt is not claimed.

The endpoint never receives an order request, never writes evidence and has
no HTTP mutation verbs. It exposes `execution_authorized=false` alongside
the audit reference.

## Validation and outstanding release conditions

Dedicated Phase 6 workflow runs:
- New journal/rollback/reconciliation/API tests plus Phase 1–5 safety suite
- Source access policy: no newly unreviewed broker methods
- Static GET-only/no-order-call audit
- PostgreSQL 16 additive schema/immutability/insertion rollback/isolated
  backup-restore and disposable-only downgrade/re-upgrade

The existing full repository CI and real-world broker/prod proofs are **not**
substituted by a targeted pass.

Remaining hard blockers:

1. The existing Sterling execution router and legacy broker writes remain
   untouched, **not protected by this standalone journal**. No mandatory
   run-time order-gate integration is certified.
2. Real broker/exposure/session/strategy-health signals are not attested.
   Phase 4 decisions remain synthetic offline validation.
3. PostgreSQL privileged INSERT/DDL, application-role grants, multi-worker
   concurrency and independently archived tamper evidence are not proven.
4. No broker order/fill reconciliation, external live order receipts, actual
   financial-position settlement or production restored database acceptance.
   Reconciliation here covers **offline queue and decision events only**.
5. External alert delivery and acknowledgement, browser/mobile operator UI
   acceptance and end-to-end failure/drift drills are pending.
6. Phase 2B legacy direct-access inventory remains open. Zero *new* patterns
   does not certify any legacy execution path as protected.

**Do not merge, deploy, activate live orders or proceed to Phase 7.**

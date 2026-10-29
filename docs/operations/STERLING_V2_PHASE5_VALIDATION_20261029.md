# Sterling V2 — Phase 5 offline scheduler and backlog validation

**Date:** 2026-10-29.  
**Decision:** targeted validation PASS; **production/live activation BLOCKED**.  
**Draft PR:** https://github.com/nageshmadaram/sterling-v2/pull/7  
**Validated implementation SHA:** `055a12b6685e4b64eb3eae2ce8507f21fedf3b10`  
**CI evidence:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40800423379

## Real repository results

| Validation check | Observed outcome |
|---|---|
| Manual, delayed, session, recurring schedules | PASS |
| Explicit pause/resume and per-item operator pause | PASS |
| Deterministic UTC due-time ordering, priority, pagination, deduplication | PASS |
| Stale-before-evaluation skip and stale requeue refusal | PASS |
| Pending, queued, evaluated, approved, rejected, paused, skipped, cancelled, emergency-stopped backlog states | PASS, tested transitions |
| Idle, queued, evaluating, risk_gate, approved, rejected, paused, completed, failed, emergency-stopped orchestration contracts | PASS, tested transition boundaries |
| Risk gate approval references actual Phase 4 decision/audit event IDs and version hash | PASS |
| Offline human approval and Phase 4 state/emergency gates | PASS, inherited Phase 4 regression tests |
| Fail-closed risk requeue and safe retry | PASS |
| Immutable lineage: strategy, version, run, risk-policy digest, batch, event chain | PASS |
| Queue growth, stale, paused, rejected, emergency events | PASS, pending-only receipts |
| SQLite append-only UPDATE/DELETE triggers and audit-insert rollback | PASS |
| Combined Phase 1–5 safety regressions | **101 passed, 7 warnings** |
| Phase 2B source access audit | **0 new unreviewed**; 272 pre-existing detections |
| Static ban on new broker / HTTP / external-network / worker paths | PASS |
| PostgreSQL 16 additive migration, immutable trigger and forced audit rollback | PASS |
| Bounded due poll / idempotent materialization on disposable PostgreSQL | PASS |
| PostgreSQL 16 pg_dump / isolated restore / row-count check | PASS |
| Disposable-only Alembic downgrade to b64f3c27e190 and re-upgrade | PASS |
| Full application regression and production release certification | **NOT CLAIMED** |

The seven warnings are inherited Python string-escape SyntaxWarnings in the Phase 2B audit script. They were not the source of a Phase 5 failure.

### Iterative failure and remediation

- Initial [run 40799884338](https://github.com/nageshmadaram/sterling-v2/actions/runs/40799884338): 97/97 targeted tests and both jobs PASS.
- After adding stale-before-evaluation and explicit queued-item pause coverage, [run 40800245521](https://github.com/nageshmadaram/sterling-v2/actions/runs/40800245521) reported 100 PASS / 1 FAIL. The test incorrectly expected an exception for a paused-gate requeue; the actual fail-closed policy returns `False` and appends `requeue_denied` to the ledger. No runtime safety check was weakened.
- Final [run 40800423379](https://github.com/nageshmadaram/sterling-v2/actions/runs/40800423379): 101 PASS, 0 FAIL, 7 inherited warnings; source access guard 0 newly unreviewed; disposable PostgreSQL validation/restore PASS.

### Implemented files and authority separation

- `backend/app/domain/offline_scheduler.py`: frozen, non-authoritative scheduling/backlog/lineage contracts and explicit transition map.
- `backend/app/persistence/offline_scheduler_service.py`: operator-invoked (no background worker) schedule creation, bounded due materialization, queue projection, risk-bound evaluation, safe requeue, per-item pause, immutable preview completion and authenticated pending alert records. The only risk evaluation call delegates to the pre-existing Phase 4 **offline** `OfflineExecutionGate.dry_run()`.
- `backend/app/persistence/offline_scheduler_models.py` and additive Alembic `c75f92d1a341`: an independent hash-chained, append-only event table. Existing Sterling order routers, broker SDK calls, strategy logic, and live execution tables were not altered.
- `backend/tests/test_offline_scheduler_phase5.py`: disposable SQLite policy, audit, lineage, timing, stop, identity and rollback tests.
- `backend/scripts/validate_orchestration_postgres.py`: refuses non-disposable PostgreSQL targets.
- `.github/workflows/phase5-offline-scheduler.yml`: targeted regression, no-direct-broker path audit and disposable PostgreSQL restore.
- `docs/architecture/PHASE5_OFFLINE_ORCHESTRATION.md`: architecture and unresolved constraints.

**A queue item in `approved` is an offline validation artifact, not an executable broker order. All outputs declare `dry_run=true` and `execution_authorized=false`.** No automated worker, HTTP API, market order route, new broker write, login authority, trading toggle, UI clone, merge or deployment was created.

### Remaining hard blockers

1. Current risk, exposure, market quality, strategy health and market-regime evidence for Phase 4 are synthetic and untrusted; no real-account data binding or independent attestation.
2. The existing order router and strategy runner paths are **not** routed through Phase 4/5. Green offline results cannot prove that legacy live order paths require risk decision receipts; a future execution integration needs separate certification and explicit authorization.
3. Phase 4 risk receipts and Phase 5 queue audit use **separate transactions**. Failure of Phase 5 leaves an unused, non-authoritative Phase 4 dry-run receipt. Production-wide atomicity, distributed lock stress and crash/restart races are unverified.
4. PostgreSQL superusers/DDL and unconstrained INSERT rights can forge/remove ledger history; deploy-time least-privilege roles, independent append-only evidence archive and isolated restore validation are still required.
5. No real scheduling worker, market calendar/session holiday rules, provider outage/clock-skew certification, durable dashboard/Telegram delivery or external operator acknowledgement. Alerts are **pending ledger records**, not sent messages.
6. Phase 2B still has 272 previously detected access patterns, including 30 legacy broker-write findings and 4 execution-authority findings; 0 *new* is not elimination of these risks.
7. Whole-repository CI, browser/mobile acceptance, broker identity/static IP, production PostgreSQL backup/restore and moving-market failure-drill receipts remain open.

**Release instruction: keep PRs #1–#7 as drafts, do not merge/deploy, do not enable live execution or any broker write path, and stop before Phase 6.**

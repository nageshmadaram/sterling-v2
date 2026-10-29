# Sterling V2 — Phase 5 offline schedule, backlog and run orchestration

**Authority boundary:** this draft adds NO trading authority, broker order path,
HTTP route, background execution worker, live mode, new execution trigger,
production deployment or existing order-router modification.

## Scheduler semantics

The operator explicitly invokes `OfflineScheduler.schedule()` to persist a
validated, bounded `ScheduleSpec`. `materialize_due()` is **manually
invoked** and performs no independent background timer or job dispatch.
Schedule modes are `manual`, `delayed`, `session` (explicit open and close)
and bounded `recurring` (cadence at least one minute; at most 500
occurrences). `set_pause()` records audited pause/resume commands. Emergency
stop in either the system-wide or per-strategy Phase 4 ledger prevents normal
queue admission; defensive pausing is permitted while resumption is denied.

All timestamps are timezone-aware UTC. Due entries are sorted by
`(due_at_utc, priority, schedule_id, occurrence)` and pagination is bounded
to 100 items per operator call. Each occurrence has a deterministic UUIDv5
derived from `schedule_id + occurrence`, so retries and repeated polls do not
duplicate a queued item. Expired/session-closed items are explicitly marked
`skipped`, never silently executed. A queued item that becomes stale before
risk evaluation is marked `skipped` with an append-only alert instead of
calling Phase 4 or being considered eligible for approval. A standalone
`pause_item()` records an operator pause of queued validation work; reopening
it requires a **fresh, approved Phase 4 dry-run** through `requeue()`.
When the Phase 4 strategy or system gate is paused, newly due work is recorded
as paused rather than queued. `cancel_schedule()` permanently closes future
occurrences and cannot be undone by a pause/resume action. An offline preview
that has already passed the gate may be marked terminally failed via
`fail_preview()`, preserving its original decision receipt and emitting a
pending validation-failure alert; it cannot be requeued.

## Backlog vs run state

Backlog: pending, queued, evaluated, approved, rejected, paused, skipped,
cancelled and emergency_stopped.

Dry-run orchestration: idle, queued, evaluating, risk_gate, approved, rejected,
paused, completed, failed and emergency_stopped.

Every status transition is validated against explicit legal edges. A transition
from queued to approved cannot skip evaluating and risk_gate. A completed run
cannot reopen. Queue state is reconstructed from immutable events; there is no
mutable 'current state' approval column.

**An approved dry-run preview does not authorize any order.** All submitted
records have `dry_run=true` and `execution_authorized=false`. There is
deliberately no 'live-ready' state or broker runner.

## Phase 4 risk decision linkage

When an operator explicitly requests `evaluate()`, Phase 5 verifies an
existing immutable, owner-scoped version/run identity and exact batch/order
intent, symbol, risk-policy SHA256 and schedule lineage. It then calls the
**existing non-authoritative** `OfflineExecutionGate.dry_run()`. The Phase 4
service independently writes risk/approval decision events. Phase 5 verifies
their immutable SHA256 chain, decision IDs, offline-only payloads and original
run/version binding before creating queue evaluation, gate binding and outcome
events. No caller-supplied 'allow' result is accepted.

Cross-ledger effects span two database transactions: the Phase 4 decision
commits first; a Phase 5 audit failure leaves an extra **non-executing**
Phase 4 decision receipt, never an approved queue item. The final Phase 5
transaction uses the same PostgreSQL advisory transaction lock as Phase 4 and
rechecks both global and strategy kill switches. Approval is refused if a
kill-switch or registry lock changes before binding. SQLite serialisation is
*not* a production concurrency proof.

`requeue()` requires an entirely new Phase 4 risk receipt, fresh verified
market-data/strategy-health observations and approved system and strategy
states. Emergency-stopped, cancelled, completed, disabled, locked and retired
items cannot be requeued. Failed requeue decisions are themselves recorded
with an audit reason; there is no retry-to-broker mechanism.

## Immutable lineage and monitoring

New additive revision `c75f92d1a341` creates only
`platform_orchestration_events`, in the isolated SQLAlchemy store. It includes
strategy/version/run foreign keys, schedule ID, item ID, actor/role, reason,
timestamp, event kind, event payload, and a per-strategy event hash chain.
SQLite and PostgreSQL BEFORE UPDATE/DELETE triggers forbid changes to event
rows; database inserts are transaction-bound. Run lineage references the
immutable strategy-version SHA, serialized risk-policy snapshot SHA, precise
batch and deterministic schedule fingerprint. No table in Kite or the
pre-existing live trading database is modified.

`OfflineScheduler.alerts()` exposes authenticated, **read-only** pending
event receipts for queue growth, stale items, rejected/paused previews,
emergency stops and invalid requeues. No Telegram/dashboard notification is
sent or claimed delivered; production outbox/ack/retry must be certified
separately.

## Limitations and release blockers

1. No autonomous scheduler, market-calendar engine, live permission, broker
   writes, workers or actual broker order path is created. This is an
   operator-initiated offline validation record only.
2. Phase 4 risk inputs remain synthetic and caller-provided offline
   observations; real broker exposure/account identity, market data, session
   limits and strategy performance are **not independently attested**.
3. PostgreSQL privileged INSERT/DDL can bypass audit triggers; production
   least-privilege roles, immutable archive and concurrent multi-worker stress
   are pending certification.
4. Existing legacy direct broker reads/writes and order router authority are
   unchanged; zero new findings does not eliminate Phase 2B exceptions.
5. No authenticated UI, device delivery, Telegram receipts or monitoring
   persistence outside the DB ledger is connected.
6. A standalone gate does not prove existing strategy order paths are gated.
   Future execution integration requires a separate reviewed plan and
   independent broker/moving-market risk certification.
7. Full repository regression, actual production PostgreSQL backup/restore,
   user/operator provision and end-to-end disconnect/restart failure drills
   are still blocked. Do not waive them on a targeted pass.

## Targeted CI evidence

`.github/workflows/phase5-offline-scheduler.yml` runs Phase 5 tests plus
previous Phase 1–4 registry/broker/market-data/execution-gate safety regressions,
the pinned direct-access inventory and a static no-broker-route/worker check.
A disposable PostgreSQL 16 service checks the new migration, append-only
triggers, insertion rollback, idempotent due materialization, backup/restore
and downgrade/re-upgrade. Downgrade is **destructive** and permitted only on
disposable CI databases.

Keep all dependent PRs drafts and `main` unchanged. Do not deploy, merge,
enable live entries, or begin a later phase without independent certification.

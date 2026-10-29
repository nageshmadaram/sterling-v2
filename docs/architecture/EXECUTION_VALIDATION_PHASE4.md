# Sterling V2 Phase 4 — offline execution risk and approval validation

**Status:** dependent draft, not merged or deployed. Current execution runtime is untouched.

## Strict authority boundary

This is a **non-authoritative dry-run**. It is not an order router, broker adapter, execution API, human-approved *live* trading switch, or a new execution authority. The standalone `OfflineExecutionGate` has no order placement, modification, cancellation or broker dependency. It is not wired into existing Sterling strategies, Kite endpoints, protective stops, live execution or risk admission. Even when a result says `decision=allow`, its immutable contract has `execution_authorized=false`. The `approved` state is explicitly **offline-gate-approved only**.

No live order submission, public HTTP route, production deployment or frontend change is introduced.

## State machine and approval

`disabled` (initial) → `validation_required` → `approved` (offline only); `blocked`, `paused` and `emergency_stop` are fail-closed. Approved strategy and system gates are both required for an offline `allow`. Live is always rejected with `LIVE_DISABLED`, independent of state. Paper auto-approval is rejected/deferred. Manual/semi-auto use exact, signed-actor offline approval records bound to the serialized input, signal, version and run for five minutes and consumed once.

System-wide transitions require a verified admin, and per-strategy transitions require authenticated owner/operator/admin. Every transition is audited in the append-only ledger. Emergency stop can only move to `validation_required` with verified admin and an incident-review reason; no direct transition to approved. Tests use synthetic actor fixtures to cover policy; the independent signed-JWT suite covers actual Sterling token signing/revocation.

## Offline risk and health checks

Each preview validates persisted strategy ownership, immutable published version SHA256, and matching validation/simulation run. It then checks configuration freshness, valid parameters, risk profile, recent performance threshold, market access, symbol validity, market/data quality, regime, provider state, exposure, daily/session loss and order sizing. Missing or untrusted offline evidence fails closed. A loss cap, degraded provider/data, or failed strategy health triggers a **recorded local emergency-stop state**.

The risk evidence fields are user-supplied **synthetic test input** until independently integrated with actual market data, broker exposure and portfolio risk services. This layer **does not certify** that reported exposure/loss is truthful. It must not be treated as a ready-to-connect live preflight.

## Append-only event trail

New additive migration `b64f3c27e190` creates `platform_gate_events` only. Events include actor ID/role, stream (strategy or global), version/run references, timestamp, reason, event type, JSON decision details, previous hash and content hash. Event types: `state_transition`, `operator_approval`, `signal_received`, `gate_evaluated`, `risk_decision`, `approval_decision`. Each preview writes its four decision events in **one SQLAlchemy transaction**. The hash chain is verified before each operation. SQL INSERT failures roll the full preview back. SQLite and PostgreSQL BEFORE UPDATE/DELETE triggers reject edits and deletions. PostgreSQL advisory transaction lock serializes offline gate transitions and evaluations with global kill checks; SQLite is limited to disposable CI testing.

Emergency stop is recorded with `alert_required=true`, `alert_delivery=pending`. **No Telegram or external alert is claimed delivered.** A production alert outbox/dispatcher and delivery receipt require a separate gated change. Privileged database INSERT/DDL can still bypass the service; hardened PostgreSQL roles and separate attestation remain pending.

**Migration downgrade is destructive to the new gate audit ledger and may only be rehearsed on disposable CI databases.** No production downgrade, data rewrite or direct registry metadata mutation is authorized.

## Validation and release gates

GitHub Actions: `.github/workflows/phase4-execution-gate.yml` runs targeted Phase 4, registry auth/immutability, broker normalization and Phase 3 market quality regression tests, source broker-access audit, and a disposable PostgreSQL 16 migration/immutable-trigger/audit rollback/backup-restore/downgrade-reupgrade exercise.

Do not merge or deploy until the PR is reviewed and these checks pass. Passing targeted checks **does not** waive the pre-existing failed full application regression, broker identity/device proof, privileged DB restrictions, operator provisioning, live safety drill or protected-order reconciliation.

### Pending release blockers

- No trusted risk evidence source, actual order admission linkage, signed human approvals for live, production broker account or risk policy certification.
- No durable Telegram/alert delivery, operator UI or emergency-stop external policy integration.
- No DB privilege proof preventing direct strategy metadata updates, rogue INSERT, or privileged DDL.
- Previous Phase 2B legacy direct access still includes 30 broker-write call sites and 4 execution authority call sites requiring risk review.
- No production PostgreSQL backup restore, failure drills, static-IP account binding, CI-wide regression and live broker proof.

**Do not proceed to Phase 5 or any broker execution activation.** Keep `main` unchanged and all dependencies as drafts.

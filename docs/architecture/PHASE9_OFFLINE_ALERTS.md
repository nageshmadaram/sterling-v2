# Sterling V2 Phase 9 — durable offline informational alerts

**Scope:** a new, independent opt-in alert/audit layer for Phase 4–8
offline strategy evidence. No broker writes, order submits, live execution,
strategic trading decisions, implicit approval, production deployment or
merges. This draft depends on Phase 8 PR #10.

## Source, severity and provenance

An alert must cite an existing, SHA256-verified strategy-owner-scoped
source record from one of:
- Phase 4 offline gate event
- Phase 5 immutable orchestration/queue event
- Phase 6 hashed execution decision
- Phase 7 explicitly **hypothetical** analytics record.

Missing source records are refused. An arbitrary external broker order
number or user-supplied performance claim cannot be promoted into a
verified alert. An audit corruption detected by Phase 6's reader is
shown as an *untrusted* UI blocker; when that same ledger cannot be
verified, Phase 9 cannot safely derive a new verified-source alert from it.
This is a deliberate integrity boundary.

The supported priorities are **info, warning, critical**. Categories:
risk threshold, stale market data, scheduling failure, invalid queue
item, invalid decision trail, approval rejection, emergency stop, invalid
strategy health, audit exception, stale provider output, negative
hypothetical KPI trend, informational update. Critical priorities are
reserved for emergency stop, invalid trail and audit exception.
Escalation is **informational-only**, with explicit operator-invoked
configured delays and durable `escalated` receipts; it does not call
any execution or risk control path.

## Immutable SQL model

New Alembic revision `f08d719bac64` adds
`platform_offline_alert_events`, separate from legacy real-order,
Telegram and execution systems. Every event contains a strategy,
deterministic per-source alert identity, ordered strategy-stream
sequence, per-alert sequence, event type, actor/role, timestamp,
audited reason and payload, prior hash, current SHA256 and a
SQL-enforced `execution_authorized=false` constraint.
Before UPDATE/DELETE triggers reject mutating published evidence
on SQLite and PostgreSQL16. Transaction failures reject the
corresponding metadata change. Per-strategy PostgreSQL advisory
locks serialize writers; local SQLite testing is not proof of
production multi-worker concurrency.

Alert state is projected from append-only `created`,
`acknowledged`, `resolved`, `expired` records. Delivery state is
projected independently per channel from `delivery_pending`,
`delivery_sent`, `delivery_failed`, `delivery_acknowledged`,
`delivery_expired` records. A SHA chain and state-transition
validation run before replay. The unique deterministic alert identity
suppresses a repeated (strategy, category, source type, source event)
for the lifetime of that source: retries never generate a second alert.
Rows are never physically deleted in Phase 9; expiry is a durable
state transition, not deletion.

Alert retention policy: retain immutable audit records indefinitely
in this slice; operators may explicitly set a future expiry timestamp
and invoke an offline administrative expiry sweep. Production data
retention, archival, privacy rules and independent WORM notarization
remain release blockers. No automatic background sweeper is enabled.

## Delivery and retries: honest capabilities

Available channel **contracts**: dashboard, Telegram, WhatsApp, email.
Dashboard delivery writes a durable accepted receipt and is visible
via signed GET; an operator can explicitly confirm receipt. For
external channels the service requires an explicitly injected,
verified-destination, enabled, idempotent transport adapter.
No network, provider API, recipient enumeration or secret is
configured or automatically invoked in the Phase 9 branch.
**No Telegram/WhatsApp/email receipt from a real provider is claimed.**

The service commits a `delivery_pending` reservation BEFORE calling
an adapter. After the call it appends `sent` plus returned receipt or
`failed` plus type-only error and exponential backoff, maximum four
attempts. A previously `sent` receipt is idempotent; failed attempts
can retry only after their delay with the **same stable idempotency key**.
An ambiguous `pending` attempt (crash/network uncertainty) is never
blindly repeated. Real provider acknowledgement is a separate
explicit receipt event. Exactly-once external delivery across provider
network failures cannot be guaranteed by database transactions alone.

There is **no background worker or outbound broker integration**.
Operator-invoked delivery and escalation functions are service-only,
not callable through the browser. No WhatsApp or email account
connection or consent is implied.

## Read-only operator UI and controlled acknowledgement

The existing Kite → More → Offline Evidence page adds a **Durable
alert inbox** displaying priority, category, source event, reason,
timeline, acknowledgement/resolution state, per-channel attempt
status and expandable immutable audit trail. Backend routes are:

- `GET /api/v1/offline-validation/strategies/{id}/alerts`
  with verified Sterling JWT, owner scope, limit and unresolved filter.
- `POST /api/v1/offline-validation/strategies/{id}/alerts/{alert_id}/acknowledge`
  with an explicit human-entered bounded reason.
- `POST /api/v1/offline-validation/strategies/{id}/alerts/{alert_id}/resolve`
  with bounded reason, **administrator-only** on the server; the
  frontend deliberately has no resolution button.

**The acknowledgement action is not a strictly read-only HTTP
operation.** It is the sole frontend-permitted metadata-only mutation,
and is not part of the trading/registry/queue/execution authority
interfaces. It appends a durable audit receipt, changes only the
alert projection, and cannot acknowledge or approve a Phase 4 order.
The UI has no alert creation, provider dispatch, strategy gating,
broker write, trading or execution control.

Alerts are informational regardless of severity. Dashboard data
fails closed when the audit ledger fails, a request is stale,
the owner/strategy differs or any response claims execution
authority; failing a provider does not imply a sent notification.

## Certification gates and residual blockers

Phase 9 dedicated CI runs focused backend alert/ack/expiry/source
integrity/delivery retry/failure/dedupe tests, Phase 1–8 safety
regressions, React dashboard plus inbox smoke, TypeScript and
frontend compile, static no-broker/no-execution route checks, and
disposable PostgreSQL16 append-only, forced rollback, pg_dump/restore
and downgrade/re-upgrade.

This offline success **cannot** certify:
- Real delivery/consent/credential safety or Telegram, WhatsApp or
  email account/channel/provider receipts;
- Actual operator device notification visibility or acknowledgement,
  background delivery worker or operational support/oncall escalation;
- Privileged database tamper resistance, least-privilege roles,
  independent WORM archive or real production backup restore;
- Real broker order/fill evidence, authentic live strategy health,
  actual profits, production trading entitlement or correct
  existing legacy order execution authority;
- Full app regression and mobile browser/accessibility acceptance.

The earlier 272 broker access patterns remain in the legacy
inventory. Zero newly unreviewed patterns cannot substitute for
live routing/remediation proof.

**No merge, deployment, live trading, broker write or Phase 10.**

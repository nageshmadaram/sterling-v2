# Sterling V2 — Phase 8 read-only operator evidence UI

**Scope:** operator-facing observability of **offline** Phase 4–7 validation
artifacts only. This draft is stacked on the unmerged Phase 7 PR #9. It
does not introduce an order entry, risk-admission override, broker SDK
dependency, live trading switch, mutation API, worker or production
deployment. It cannot authorize any operation.

## Integration and product consistency

The new **Offline Evidence** tab is an opt-in, lazily loaded view under
**Kite → More**, next to the existing live Operations tab. It uses the
existing Sterling Kite theme variables (`--k-bg`, `--k-surface`,
`--k-text`, `--k-brand`, `--k-border`) and small, responsive cards and
tables. Mobile navigation uses the existing horizontally scrollable More
tabs; Phase 8 does not alter the primary navigation or duplicate the
Operations UI. No trade controls appear in this panel.

## Backend sources: GET-only, signed, owner-scoped

Previously validated Phase 6–7 read-only endpoints:
- `GET /api/v1/offline-validation/strategies/{id}/decisions` — verified
  immutable Phase 6 risk, approval, reason, and journal hash references
- `GET /api/v1/offline-validation/strategies/{id}/reconciliation` —
  queue/scheduler/reconciled outcome and stale/audit issues
- `GET /api/v1/offline-validation/analytics/strategies/{id}/summary`
  — **operator-supplied hypothetical** metrics, equity curve, KPI warnings
- `GET /api/v1/offline-validation/analytics/strategies/{id}/comparison?group_by=version`
  and `/.../symbols?group_by=symbol` — version/symbol breakdown

Phase 8 introduces two strictly GET-only, JWT-verified and registry-scoped
metadata views:
- `GET /api/v1/offline-validation/strategies` — operator-owned
  versioned strategy selection (bounded to 200, no other owner disclosed)
- `GET /api/v1/offline-validation/strategies/{id}/status` — immutable
  version IDs/fingerprints, registry state, **verified Phase 4 offline
  global/strategy gate status and approval policy**. It also projects
  append-only Phase 5 schedule definitions and operator pause/cancel
  receipts (configured/paused/cancelled, bounded occurrences and
  materialized queue-record count). This is observation, not a timer
  or worker.

The status API explicitly returns a NULL strategy-health score because
independently authenticated live health evidence is not available.
`live_execution_enabled=false` here refers **only to this offline
validation slice**, not a certified reading of Sterling's legacy broker
execution switches. No UI status text claims those legacy paths are
blocked, because Phase 8 has not modified them.

## Presentation and safety model

The overview contains hypothetical net P&L, win rate, profit factor,
expectancy, maximum drawdown and active-day Sharpe; a synthetic equity
curve; risk/strategy status; version/symbol breakdown; rolling metrics;
and an informational alert panel. The decision tab shows immutable
decision reasons, risk/approval state, quality status, UTC timestamps
converted to IST for display and expandable SHA256 provenance.
The queue tab shows offline backlog, reconciliation state and audit
references without schedule or requeue controls.

Responses are fetched solely with the pre-existing authenticated
`api.get` API client and React Query, with 30-second read-only
refetches. There is no `post`, `put`, `patch`, `delete` or WebSocket.
The UI rejects any source with `execution_authorized != false`, wrong
hypothetical provenance, wrong strategy ID, or inconsistent source
contracts. Missing/failed/stale (90-second read age) or untrusted audit
data hides metrics instead of continuing to paint cached figures.
Empty history is labeled as unavailable, never as profitable or safe.
A manual Refresh only invalidates read queries.

Alert priorities are displayed as info, warning and critical, with
critical first. These are **informational screen warnings only**; there
is no alert dispatcher, Telegram delivery or broker kill action.
Unknown or unauthenticated real exposure and strategy-health scores
are **not simulated** in the presentation.

## Validation scope

Targeted UI smoke tests: strategy selector and dashboard, hypothetical
metrics and equity curve, risk and versions, decision hash provenance,
backlog and alerts, empty/loading/unavailable/degraded/stale states,
cross-tenant/mismatched strategy data, and fail-closed authoritative
source rejection. Static validation forbids HTTP mutation calls or
order/exec broker references from the new UI. Backend GET/auth tests
verify owner scoping and Phase 4 system/strategy state provenance.
The workflow also retains Phase 7's targeted backend safety tests,
baseline broker audit and disposable PostgreSQL restore.

## Remaining blockers — not production-ready

1. Phase 7 KPI numbers use **explicit synthetic assumptions**, not
   authenticated historical broker fills, proven returns or verified
   real-account exposure. No evidence here establishes profitability.
2. No backend risk health score or live broker safety posture is
   independently attested. Offline gate approval cannot authorize live
   trading and does not protect existing legacy execution routes.
3. The Phase 6 reconciliation API can return an `untrusted` audit; UI
   intentionally hides all KPI figures in that state. The standalone
   journal/queue remains separated from live broker order/fill history.
4. Network and signed-auth evidence, production DB permissions, WORM
   archival, actual device/touch acceptance, visual capture, full
   repository regression and real broker drills remain unverified.
5. Source datasets may be empty because the offline journal is opt-in
   and no production deployment or migration was performed; the
   frontend shows an honest empty state.
6. Prior 272 broker direct-access findings remain unresolved. Zero
   *new* unreviewed methods cannot be construed as certifying existing
   order dispatch.

**Keep PRs #1–#10 as drafts, main unchanged. No merge, deployment,
live switch, broker write, production release or Phase 9.**

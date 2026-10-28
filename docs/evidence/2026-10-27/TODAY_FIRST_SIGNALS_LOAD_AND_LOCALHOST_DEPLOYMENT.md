# Today's signals first — four-strategy workspace load priority

Date: 2026-10-27. Branch: dev. Status: source committed, full integration
tests and deployment NOT executed on the operator's Ubuntu host.

## Operator request

1. Current IST trading-day signals from SuperTrend (including Navigator
   confluence), Snapback, Gamma-Unwind and EMA Trend take precedence.
2. Older history is requested/rendered only after all four current-day
   strategy reads have completed. Do not make a slow historical backfill
   block the current day's entry view.
3. Show progress/blocked sources clearly instead of an apparently blank board.
4. Keep open positions, still-actionable prior-close Swing signals and all
   existing live order, safety and risk gates intact.
5. Distinguish committed GitHub code from a version actually deployed on localhost.

## Source changes

- The persistent `KiteLayout` wraps all boards in
  `TodaySignalPriorityProvider` and displays the accessible
  `TodaySignalPriorityStatus` with per-strategy loading/scanning/ready/error
  state. The gate is specific to the current IST date; on day rollover it
  evaluates anew. A failed current-day source does not quietly release
  historical results as a substitute.
- SuperTrend's `/api/v1/kite/engine/signals?today_only=true` returns
  signals from today's IST session **plus ongoing active positions**.
  The normal unrestricted endpoint stays backward compatible. React Query's
  today-only cache is separate from the full-history cache; a completed
  scan invalidates/refetches the today-only response, without incorrectly
  inserting unrestricted results into it.
- Snapback's on-demand historical signal query is disabled until the
  workspace gate releases. Prior-close **armed Swing candidates** remain
  visible when valid for the next-session opening entry; restored, inactive
  historical rows do not get mistaken for today's trade opportunities.
- Gamma's recent-history query, historical-day replay and automatic
  multi-session backfill wait until the current-day barrier. Its live
  snapshot continues to refresh independently.
- EMA Trend requests its `day=YYYY-MM-DD` server-filtered snapshot
  first, then switches to the existing unrestricted saved signal query.
  Existing open campaigns remain in the position panel.
- The workspace status explains when today's four strategies have been
  checked, why older rows are waiting and whether a feed failed.
- SuperTrend mode settings now identify the fixed original Swing
  60-minute Heikin-Ashi input as part of Swing, without a redundant
  standalone hourly selector. An expanded settings comparison shows
  entry conditions, bar durations and holding ceilings for all modes.
  No indicator or original strategy rule changed.
- `KiteFooterStatus` shows the actual built frontend checkout SHA (or
  UNKNOWN) so a localhost page can be distinguished from remote GitHub
  commits after the running build is updated.

## Limits and safety invariants

- Only the four maintained strategy surfaces above participate in this
  gate. Navigator signals share SuperTrend's endpoint. This is **not**
  an exchange-wide order-authority scheduler.
- Current-day read completion is not proof that any eligible signal exists.
  Every strategy's server-side quote, broker, risk, stop and session admission
  remains authoritative. Historical rows and partial Snapback scan previews
  are not executable.
- The existing Snapback current snapshot can contain a small EOD
  catch-up window of historical signals because it also serves its
  next-session Swing admission; those are withheld in the board until
  the gate releases, except for still-armed carry-forward signals.
- Exact-time polling and backend scan completion can still depend on
  Kite network/data availability; no market-to-row latency measurement
  was taken on the deployed app.
- Full frontend tests, TypeScript/Vite production build, complete backend
  tests and actual broker/localhost rollout were **not executed** in this
  conversation. Added regression tests are **committed, not claimed passed**.
- GitHub dev and the local Ubuntu server are separate environments.
  No remote shell, file synchronization or authenticated deployment
  to the user's `~/Sterling` localhost was performed. There is no
  basis to claim visible localhost changes yet.

## Regression tests added / updated

- `frontend/src/components/kite/board/__tests__/TodaySignalPriority.test.tsx`:
  four-source barrier, failed source, active scan, IST date rollover.
- `frontend/src/hooks/__tests__/useEmaTrend.test.tsx`: EMA dated current
  snapshot query followed by unrestricted history.
- `backend/tests/api/test_kite_engine_today_priority.py`: SuperTrend current
  IST-day response, active older positions, invalid old timestamps, no mutation.
- `frontend/src/components/kite/board/__tests__/StrategyModeInfo.test.tsx`:
  mode entry timings and fixed original Swing explanations.
- `frontend/src/components/kite/__tests__/KiteFooterStatus.test.tsx`:
  frontend build identity visible without guessing deployment.

## Release disposition

Source progress: committed to dev. Deployed: **NO**. Full application QA: **OPEN**.
Broker live functionality and positive after-cost forward profitability:
**UNVERIFIED**. Do not merge this broad untested branch into main,
activate real-money new entries or issue an unsupported certification.
The operator is receiving hospital treatment and should not be asked
to perform on-host deployment commands from the ICU.

## Secondary-query audit follow-up (27 October 2026)

Previously overlooked consumers of the unrestricted SuperTrend or EMA
signal history were located by inspecting the actual dev source, not by
assuming the workspace barrier covered every component:

- `AdaptiveEdgeRightSidebar`: its tab counts independently requested
  unrestricted SuperTrend and EMA histories while the four-strategy
  priority barrier was still closed. It now subscribes to the shared
  today-only SuperTrend query and the identical dated EMA query until
  `olderReady`; after the gate releases, those two return to normal
  history reads.
- `KiteStartupSurfaces.EngineStartupBoundary`: its boot readiness check
  now reads the today-only SuperTrend projection rather than the full
  historical journal.
- `TradeRulesPanels.AutomaticRulesPanel`: the data read used for current
  solvability uses the today-only projection instead of full history.
- `EnginePositionsPane`: its signal read uses the today-only projection,
  which still explicitly includes older **active** positions. The
  separate position inventory endpoint is unchanged.
- `TodaySignalPriorityStatus`: the banner shows the IST day, strategy
  response/scanning/error states, and explicitly distinguishes a
  successful API snapshot from a completed current-day scan or a
  nonzero signal count.

This repairs four independent early unrestricted-fetch paths, but does
**not** imply a server-side broker-wide scheduling guarantee: unrelated
screens and scheduled backfills have separate lifecycle constraints.
The Snapback snapshot itself can still include historical carry-forward
candidates required for next-session Swing; its board presentation gates
those historical rows as specified above.

### Deployment and verification boundary

Latest commits for the audit exist on remote `dev`. They do not update
the running Ubuntu checkout, its backend process, Vite dev server or
previously built assets. `VITE_BUILD_SHA` is resolved when the Vite
server/build starts; a long-running server may need a controlled restart
to display the new Git identity. Before any restart an authorized operator
must confirm that open positions, protective orders and exit supervision
are not disrupted.

Source-level review confirmed that the four affected consumers now
request today-first projections; the existing provider regression
assertions were updated to reflect truthful API-response wording and IST
date. **No full TypeScript/Vite build, browser test, backend test,
localhost restart, broker integration or P&L certification has been
executed in this GitHub-connector-only continuation.** Treat all of
those as OPEN, not PASS.

To inspect the current host later without modifying its checkout, an
authorized operator can record `git status --short --branch`,
`git rev-parse HEAD`, `git rev-parse origin/dev` (after a fetch),
`./sterling.sh status`, and the browser footer's build SHA. Do not
force-pull, overwrite uncommitted changes or restart a live risk manager
based only on remote-branch updates.


## Snapback UI freshness follow-up (28 October 2026)

Review of the actual React Query hook found that a **live idle Swing**
Snapback snapshot refreshed only once every **30 seconds**. While the
today-first barrier waited, the other three strategies could refresh at
3–7.5 second intervals yet Snapback could display stale completed-scan
rows and hold its status longer than necessary.

The polling hook now requests the **read-only** Snapback snapshot every
**5 seconds while idle**, every **1.5 seconds while scanning**, and every
**1 second in replay**. It retains the same React Query cache identity,
server endpoint, mode/config and broker admission controls. Polling may be
delayed by an inactive browser tab, network, an overloaded backend or scan
completion, so this is **not** a guaranteed signal-generation-to-entry
latency SLA. It never triggers a new scan or submits a broker order.

The priority banner also counts a received API snapshot in the
`scanning` state as a response, while preserving the historical-query
barrier until the scan settles. Added narrow polling and banner tests, but
**no full Vitest, backend, device, or Kite tests have run on this new
source commit**. GitHub Actions remains blocked before runner assignment
(#285); deployment evidence remains blocked (#277).


## Active Kite account switch must not reuse signal snapshots (28 October 2026)

A further source audit found that `useActivateKiteAccount` previously
invalidated only `kite-accounts` and `kite-status`. The four live strategy
query caches — SuperTrend, Snapback, Gamma-Unwind and EMA Trend — could
retain the previous account's response during the new account's refresh.
The today-first release barrier was keyed only by view and IST date, so it
could remain released for the previous account even while the new account's
current-day data had not been obtained.

The account-change mutations now reset broker-account-dependent React Query
entries, including strategy snapshots/history and Kite positions/orders,
before treating a new identity as current. The status cache is updated from
the confirmed active Kite account, and `TodaySignalPriorityProvider` keys
its historical-read release by **view + active Kite account + IST date**.
Deleting an account, logout and an identity-changing new/renewed session
also refresh broker-dependent caches. Simulation/live still have their
existing independent query clients.

The reset concerns **frontend GET response caches only**; it does not
wipe any broker order, portfolio or persistence record or request a
new market scan. Tests cover the cache predicate and reset, and the
day/account gate transition, but they are **not verified as executed**
under current GitHub CI runner allocation blocker #285. Owner/device
deployment evidence also remains open under #277. Do not use cached
UI rows as an execution authorization or claim broker readiness.


## Minute-bar event time must use completed candle, not candle open (28 October 2026)

The Snapback engine supplies `timestamp_ms` as the source candle's
**opening** timestamp. Intraday/scalp/ultra/overnight signals are only
defined after that candle closes; the backend also provides
`signal_close_ms` from `signal_close_timestamp_ms` in its engine metrics.
The prior board used `signal_close_ms ?? timestamp_ms`, so a missing
close (for example, a restored/legacy row) was labelled at the candle
opening time even though no signal could yet have been generated.

The board now accepts the minute-bar close only when both open and close
are valid integer timestamps and `close > open`. Otherwise it shows
**Close time unavailable** without an invented time. Original Swing
signals continue to be explicitly marked **day close**, because their
daily-candle logic is not an intraday entry method. This is presentation
integrity, not a scan frequency change or permission to submit an order.
New regression tests cover missing, zero, nonfinite, reversed and
equal timestamps. Full CI/host execution still blocked (#285/#277).

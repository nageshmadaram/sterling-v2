# EMA Trend: urgent operator and developer handover

Updated: 2026-10-17. Strategy ID: `ema_trend`.
Repository: https://github.com/nageshmadaram/sterling (private).
Draft PR: https://github.com/nageshmadaram/sterling/pull/243
Review branch: `fix/ema-trend-gap-continuity-20261017`

## Safety posture — read first

**NOT CERTIFIED FOR LIVE MONEY.** The EMA implementation is restricted to virtual/paper
execution. No real broker stop-loss guarantee, real-fee acceptance, stable profitability,
or independent operational certification exists. Do not enable broker orders or rely on
this for household expenses or debt repayments. Respect the global live-entry OFF gate.

Emergency user actions in the EMA board apply to **EMA virtual campaigns only**:
- **Pause paper entries** stops new exposure but continues campaign management.
- **Flatten all paper** pauses entries and requests closing EMA virtual positions;
  an `UNRESOLVED` response means orders/fills still need reconciliation.
- A full individual **Close paper campaign** request remains available if a BUY or
  reduction is pending, even with zero confirmed shares.
- The UI retains campaigns with disputed orders even if their projected state is
  `CLOSED`, and does not allow new entries to resume before flattened inventory and
  outstanding orders reconcile.

These controls **do not cancel live Kite broker orders** or flatten external holdings.
For a real account, verify orders and positions directly at the broker.

## Branch state

EMA implementation exists in backend engines/services/API and frontend board,
settings, charts and replay. Draft PR #243 adds missing-bar refusal, exchange-aware
admission, durable flatten intent, pending-buy cancellation/submission veto,
duplicate-close avoidance, offline-intent persistence, operator UI, and resume
interlock, with regression test cases.

Do not cherry-pick isolated commits from the draft without their companion tests.
Do not merge the draft until verification passes.

## Reproducible acceptance commands

Run on an isolated clone/checkout with correct dependencies and local environment.
**Do not reset the operator's modified working tree**. Ensure backups and ample
disk space before running tests. These commands are a verification recipe, not
evidence they have passed:

```bash
git fetch origin
git switch --detach origin/fix/ema-trend-gap-continuity-20261017

cd backend
python -m pytest tests/engines/ema_trend tests/services/test_ema_trend_runtime.py tests/api/test_ema_trend_api.py -q
cd ../frontend
npx tsc --noEmit
npx vitest run src/components/kite/board/__tests__/EmaTrendTicket.test.tsx src/components/kite/board/__tests__/emaTrendAdapter.test.ts --coverage=false
npm run build
```

Then run all backend tests, all frontend tests, integration tests, and actual
virtual-exchange failure drills (partial fills, unknown submission, order
cancellation unknown, reconnect/restart, browser close, stop gap, daily cutoff,
stale quote, and tenant isolation). Prove the simulated exchange cannot mutate
the real broker. All outputs must be retained in release evidence with date and
commit SHA.

## Verification constraint

GitHub Actions on draft PR #243 has reported failed workflow results. The
available GitHub job records expose no executed test steps and terminate
rapidly. The downloadable job logs return `BlobNotFound`. Consequently there
is **no trustworthy executed test result** for the current draft branch from
this session. Investigate Actions runner availability and billing/permission
policy before interpreting CI as a code failure. Do not mark tests PASS.

## Remaining blockers

- Verify pytest, typecheck, Vitest, build, E2E and failure drills.
- Verify EMA live scanning / context readiness with exchange-sourced data and
  independent date/calendars. Historical BSE long-context evidence is limited.
- Run deterministic replay and research controls; record negative results.
- Verify backups and isolated restore of persisted campaign/order/ledger facts.
- Certification for real broker execution/protective orders is absent, and EMA
  real-order eligibility must stay closed.

## Minimal decision

`REVIEWABLE_PAPER_CANDIDATE` — code changes recorded, tests unverified.
`LIVE_ELIGIBILITY = BLOCKED`. Keep the PR draft until tests and operational
acceptance pass. CI/CD rollout and promotion remain the final step only.


## Live execution migration: exact status

The operator explicitly requested broker LIVE execution for EMA. Strategy-level
entry, EMA partial exit, stop and trailing decisions exist, but they currently
produce virtual broker orders only; **no EMA live order adapter exists**.

Added a read-only `GET /api/v1/ema-trend/live-readiness` preflight and an explicit
`Check Kite live readiness` button in EMA Settings. The diagnostic validates
active account type, freshly checks Kite identity, reads positions, holdings
and capital, and reports external symbol-ownership conflicts. It performs no
broker order/GTT writes and never grants LIVE eligibility.

Reasons not yet activatable:
- Existing `execution.py` requires a concrete `SimulatedKiteClient` and uses
  virtual UID/account/ledger/fill projections.
- `release_scope.py` authorizes EMA only for simulation UIDs.
- Swing CNC requires a separately attributable real inventory/holdings, sell
  authorization (CDSL eDIS/PoA), durable GTT ownership and child-order recovery.
- Intraday/scalping/ultrascalping MIS cannot use the existing GTT helper,
  which rejects the MIS product; an independently verified stop-order and
  forced-exit lifecycle must be implemented.
- Broker-side protective GTT submits LIMIT orders when triggered. A trigger,
  including an acknowledged GTT, is **not** a confirmed exit fill.
- User-specific live broker login, holding authorization, deployment identity,
  account-level exposure, outage response and release acceptance are unverified.

The supported next implementation is a separate account-bound live lifecycle,
using `CanonicalExecutionService` and canonical broker GTT/order reconciliation,
rather than rewriting the simulation account identifiers or flipping paper
permission flags. Test every transition including partial fills, order timeouts,
restart and external/manual position changes. Do not send live money until
that independently passes and the owner explicitly enables bounded live risk.

Kite reference: https://kite.trade/docs/connect/v3/gtt/

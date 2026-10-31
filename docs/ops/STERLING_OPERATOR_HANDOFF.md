# Sterling operator handoff

> **Historical operational handoff, not release approval.**
> Current status: **PRE-PRODUCTION / NO-GO; live trading NOT AUTHORIZED**.
> The current evidence and external control gates are in
> [the 31 October review](../implementation/2026-10-31-preproduction-closure.md).
> Do not run live-entry enablement steps without independent release approval.

Prepared 11 October 2026 for SuperTrend, Snapback and Gamma Unwind.
Read alongside [the audit and implementation ledger](../design/STRATEGY_PRODUCTION_AUDIT.md).

## Current delivery

The three signal boards share columns, settings and readable contracts. Expand a row
for strategy-specific evidence and a position estimate. The Trade cell uses the
strategy's existing server authority; a grey control must show its actual refusal.
Charts use the exact raw exchange/contract identity, even when the displayed name
is shortened. Help explains each strategy. Terminal capture beside Clear downloads
redacted frontend/network/backend/engine diagnostics.

This checkout has been tested without a broker account. The cloud setup is local
credential-free development, with the master new-entry switch disabled. It is not a
funded production deployment. No real order, broker stop, fill or Telegram receipt
was submitted or certified by these tests.

## Preparing the production account

1. Keep new entries off while configuring the deployment. `STERLING_LIVE_EXECUTION=0`
   is the master new-entry kill switch; product default when unset is OFF (the fail-closed startup default). Readiness
   identifies this as the operator master switch. Restart the supervised backend
   after changing this environment setting; it is resolved at process startup. Exits,
   protection and reconciliation use their own continuing lifecycle.
2. Use production authentication, HTTPS, durable database/intent/backup volumes,
   and a supervised backend. Development's local anonymous identity is not a
   production login. Save restoration instructions with the deployment operator.
3. Connect the intended live Kite account in Connect. Confirm the account shown
   by Sterling matches the broker account and that the daily session is valid.
   An authorized family operator needs their own Sterling access and the account
   holder's approved broker access arrangements; do not put credentials in logs
   or this repository.
4. Verify the deployment's actual outbound IP and the broker allowlist. The local
   instance currently reports unverified/unregistered egress. Do not enter an
   invented IP to turn the readiness indicator green.
5. Set capital, per-trade risk, cash/lot caps and an account daily-loss limit in
   Trading Config. An option lot comes from its dated instrument identity, not
   a remembered NIFTY/BANKNIFTY multiplier. Keep money needed for living expenses
   separate from the trading allocation.
6. Review readiness and finish broker reconciliation. Resolve unresolved orders,
   foreign exposure, missing protection, unhealthy runtime and recovery reasons
   using actual broker evidence. Never clear an uncertain order by clicking entry again.
7. Use Broker or Both stops for supported live entries. Confirm the broker-held
   stop, quantity and product after a fill. Gamma live swing before T1 remains
   unavailable because its underlying-close stop is not broker premium protection.
8. Run moving-market, read-only acceptance with the real account and dated market
   data. Existing `scripts/sterlingctl acceptance` collects vendor/timestamp/depth/
   reconnect/persistence evidence without placing an order. Its TEST_ACCEPTANCE
   evidence does not become profitable-trade evidence.
9. Check the exact deployed source/build, server settings and current session before
   enabling entries. ALGO also needs both the master control and the strategy's own
   switch. A green switch requests entries; admission can still refuse them.

## Entering and managing a trade

Choose the strategy, inspect the instrument and expiry, then expand the signal.
Check quote freshness, planned quantity, premium outlay and defined stop loss.
The calculator's “Position estimate” describes a plan; Positions and the broker
confirm actual holdings. A bearish thesis expressed as a bought put is still a BUY.

Snapback publishes a fresh plan and arms its exact revision. Gamma can execute ENTRY
tier only. SuperTrend manual entries pass the engine's protected order path. Historical,
modeled, stale, ended, unknown-lot and watch-only rows do not gain entry authority from
an unlocked generic ticket. Read the refusal before changing configuration.

After submission, wait for confirmed broker status. Check actual filled units and
stop coverage. If an order times out, reconcile its broker identity; a timeout is
not evidence that it failed. Avoid another press while the outcome is unknown.

Use the strategy-owned exit or confirmed Positions workflow to reduce a held position.
The display's TSL, target and red counter are planned/rule values, not a broker fill.
SL/TSL outcomes can differ because of gaps, slippage and charges. A displayed target
is absent when the strategy does not define one.

## Halting and recovering

Trading Config provides Square-Off All and Kill Switch. Square-off uses owned exit
paths; confirm resting orders and final holdings in the broker, especially after a
network interruption. Safe mode blocks entries while uncertainty is unresolved;
it does not establish that the account is flat. Restarting the backend is not an exit.

Capture diagnostics, preserve intent/journal records and reconcile before resuming.
The capture is a bounded recent-history export, not a complete persistent recording.
Global backend logs require a Sterling administrator; unavailable source status appears
in the bundle. Export never needs request bodies, passwords or broker tokens.

## What replay can establish

- SuperTrend reuses completed 1H underlying signal/exit rules. Its shared replay
  option fills/marks are theoretical delta estimates. Premium-source/derivatives/
  confluence live execution parity requires separate actual option evidence.
- Snapback shared replay covers the swing daily rules and model lifecycle. It
  cannot validate the scalp lane from the swing model.
- Gamma replay shows immutable recorded E1 or historical minute-proxy observations.
  It reports data coverage and emits no invented trade P&L. Minute bars cannot
  establish live-tick event order, spread or a fill.

Each signed-in operator sees only their own replay. This server currently has one
replay slot: another operator receives a busy refusal until the owner stops and
clears the session. Force restart cannot replace another operator’s session.
Replay state is process-local and is lost on restart. Use one API worker for
replay; a shared replay store and cross-worker coordination are not implemented.
Replay does not suspend reconciliation or rule/session exits for existing positions.

Replay rows are read-only and use the replay clock. A missing signal may mean missing
history/warmup, not “no opportunity.” Never combine modeled/virtual results with actual
broker fills in an income report.

## Establishing a trading edge

The retained SuperTrend study found its long ATM/OTM option lens negative after costs
across the tested exit settings. Better deep-ITM/futures modeled findings are not a
validated income source. The software work does not reverse that research result.

Collect dated option prices, spreads, fills, costs and broker-confirmed exits. Freeze
strategy settings before a fresh out-of-sample/walk-forward period. Record trade count,
net expectancy, drawdown and loss streaks, and whether results survive costs and the
number of variants tried. Compare modeled prices to executable quotes and reconcile
all open/closed quantities. Increase exposure only after evidence supports the vehicle,
lane and risk budget. Trading profits cannot be promised or treated as family income
without that record.

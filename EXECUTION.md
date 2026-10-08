# Execution — signal to broker

Sterling places real orders on Zerodha Kite. There is one execution path and one
operator switch. This page says what the path does and what can stop it.

## Stopping new entries

Three levers, from slowest to fastest. All of them gate **new exposure only**.
Exits, protective stops, protection repair and reconciliation always keep working;
a switch that stopped Sterling closing a position would be a way to lose money.

| Lever | How | Takes effect |
|---|---|---|
| Master kill switch | `STERLING_LIVE_EXECUTION=0` (also `false`, `off`, `no`, `disabled`) in the environment, then restart the backend | On restart; survives restarts |
| Safe mode | `./scripts/sterling-safe-mode on "reason"` or the UI | Immediately; survives restarts |
| Runtime halt | `POST /api/v1/kite/trading/kill-switch` (the Kill Switch & Halt button in the UI) | Immediately; persisted in the control plane |

`STERLING_LIVE_EXECUTION` is `true` when unset. An unrecognised value (a typo)
also turns entries **off** and logs an error. `GET /health` reports
`live_execution`; `GET /api/v1/ops/live-status` shows the switch together with
safe mode, per-account recovery state and exposure freshness
(`NO_LIVE_ACCOUNT` means no stored account is live and bound yet).

## Strategies that may originate

Only the release allowlist in `app/core/release_scope.py` may open new exposure:

| Strategy | Id |
|---|---|
| Supertrend | `sterling-kite` |
| Snapback | `snapback` |
| Gamma Unwind | `gamma_move` |

Adaptive Edge, Intraday, Opening Volume and Navigator are maintenance-only: they
may reconcile, protect and exit positions they already hold, and never originate.
There is no per-strategy "family" suppression; all three strategies run.

## Canonical path

Every exposure-increasing entry goes through
`app/services/execution_service.py` (`CanonicalExecutionService`) and the durable
journal in `app/services/kite_engine/order_journal.py`:

1. Resolve the effect of the order explicitly (increase, reduce, protect, cancel).
   An order whose effect cannot be classified is refused.
2. Require a request-bound `RiskApproval` for the exact order being sent.
3. Ask `SafetySupervisor` for admission, twice (before reserving, and again
   immediately before the broker call): safe-mode file, control-plane recovery
   state, unresolved journal intents, the runtime halt, the daily-loss halt
   (soft -1,000 INR, hard -1,500 INR by default), and external (broker-side)
   exposure.
4. Require fresh broker capital evidence, then apply the day-one envelope
   (`app/core/live_minimum.py`: loss budget, gross exposure, optional lot cap, no
   averaging down).
5. Require an explicit broker identity: the account must be live and bound to a
   Kite user id. A client whose mode is missing or unknown is refused.
6. Reserve an immutable intent in SQLite, scoped to user and broker account.
7. Atomically claim `RESERVED -> SUBMITTING`. Only one caller can win.
8. Submit once. Record the broker acknowledgement.
9. If the outcome is ambiguous the intent becomes `UNKNOWN`, the account goes to
   `RECOVERY_REQUIRED`, and **nothing is resent**. Reconciliation against the
   broker decides what happened.
10. Confirmed fills feed the position and protection lifecycle (broker GTT and/or
    server-side monitor, according to the strategy's `stop_mode`).

A timeout is not permission to replay an entry.

## Strategy conditions on top of the path

- Supertrend auto-entry requires `max_daily_loss_pct` to be set and a protective
  stop (`stop_mode` `broker` or `both`).
- Gamma Unwind live intraday entries need a broker-held stop (`broker` or `both`);
  live swing entries are refused.
- Sizing is capped by `risk_pct`, `max_lots` and `execution_lots`.
- Entries are refused outside market hours and on stale signals or stale data.

## Generic Kite API writes

The generic Kite REST facade is not a second execution authority. Exposure-increasing
generic writes (unsupported cash orders, generic order modification, generic GTT
creation or modification, mutual-fund purchases and SIPs, ATO alert orders) are
refused. On a live account the generic facade also refuses GTT delete and generic
order modification: live cancel is limited to unresolved ENTRY orders, and exits and
protection go through the canonical and dedicated paths (or the Kite app). Manual
derivative entries that are admitted go through the same validated entry and
protection path.

## Contributors

Do not add a direct broker-entry call. Route it through the canonical service and
keep `app.core.lane_registry.entry_transport_allowed(client)` in front of any
remaining legacy transport; a regression suite tracks them. Test doubles exist for
the unit suite only: no production path constructs one or defaults to one.

Operational procedures: [docs/operator/START_HERE.md](docs/operator/START_HERE.md).

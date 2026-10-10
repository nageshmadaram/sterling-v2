# Risk management

Risk is separate from strategy code. Every control below is **fail-closed**: if it
errors or cannot read its input, the new entry is refused. None of them blocks an
exit, a protective stop, protection repair or reconciliation.

## Controls on every new entry

| Control | Refuses new entries when |
|---|---|
| Master kill switch (`STERLING_LIVE_EXECUTION`) | set to `0`, `false`, `off`, `no`, `disabled`, or unrecognised |
| Release allowlist (`app/core/release_scope.py`) | the strategy is not Supertrend, Snapback or Gamma Unwind |
| Safe mode (`data/safe_mode.json`) | SAFE_MODE is on, or its file is missing or unreadable |
| Recovery state | the account is `RECOVERY_REQUIRED` (after a restart, an unknown order outcome, or a failed reconciliation) |
| Unresolved journal intents | any order intent for the account is not resolved |
| Runtime halt (`/api/v1/kite/trading/kill-switch`) | the operator engaged it |
| Daily-loss halt | realised loss for the day reaches the hard halt (default -1,500 INR; soft warning at -1,000 INR) |
| External exposure | the broker was not asked what the account holds in the last 60 seconds, or holds exposure Sterling did not originate |
| Broker capital evidence | the broker's available funds were not freshly verified |
| Broker identity | the account is not live, not bound to a Kite user id, or its mode is unknown |
| Idempotent journal | the same intent is already reserved or sent (`RESERVED -> SUBMITTING` is claimed atomically; an `UNKNOWN` outcome is never resent) |
| Market hours and freshness | the market is closed, the signal is stale, or market data is stale |

Safety admission is asked twice per entry: before the intent is reserved and again
immediately before the broker call.

## Day-one envelope (`app/core/live_minimum.py`)

A wall in front of every entry, configured from the environment (blank = not set):

| Setting | Effect |
|---|---|
| `STERLING_LIVE_MINIMUM_DAILY_LOSS_INR` | stop new entries once realised loss reaches this (default: the daily-loss hard halt) |
| `STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR` | ceiling on capital deployed in open positions plus the new order |
| `STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT` | share of verified capital that may be deployed (default 100) |
| `STERLING_LIVE_MINIMUM_MAX_LOTS` | cap on lots in one entry; start with `1` |

Averaging down (adding to a losing position) is always refused. A setting that is
present but unusable is reported and entries are refused until it is fixed; it is
never quietly replaced by a default.

## Sizing and protection

- Supertrend: `risk_pct` of available F&O capital per trade, capped by `max_lots`;
  auto-entry requires `max_daily_loss_pct` and `stop_mode` `broker` or `both`.
- Gamma Unwind: `execution_lots` (default 1); live intraday requires a broker-held
  stop (`broker` or `both`); live swing is refused.
- A position whose protection cannot be confirmed is flagged `UNPROTECTED`; repair
  it or exit it.

## Breakers

The drawdown circuit breaker (`app/engines/risk/circuit_breaker.py`,
`app.state.dd_circuit_breaker`) and the execution circuit breaker
(`app/services/execution/circuit_breaker.py`, `app.state.circuit_breaker`) are
distinct singletons, see `CLAUDE.md` for the wiring invariants. The standalone
`RiskEngine` (`app/engines/risk/engine.py`) is a registry-driven rule evaluator
that is not in the order path; the controls above are authoritative.

## Rules of thumb

- Never raise size after a loss, or because things are going well.
- A refusal is information. Do not look for a way around it; read its code.
- If Sterling is not NORMAL, keep new risk blocked until the cause is resolved.

Every rejection carries a machine-readable `code` for alerting. Execution
decisions are recorded in the order journal.

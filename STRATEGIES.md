# Strategies

Sterling originates real orders for exactly three strategies. They are the
release allowlist in `app/core/release_scope.py`; nothing else can open new
exposure.

| Strategy | Id | What it looks for | Live conditions |
|---|---|---|---|
| **Supertrend** | `sterling-kite` | Three SuperTrend lines of different speeds agreeing on a direction, on index and stock derivatives (engine: `app/engines/sterling_kite_engine`, service: `app/services/kite_engine`) | Auto-entry needs `max_daily_loss_pct` set and a protective stop (`stop_mode` `broker` or `both`); sized by `risk_pct`, capped by `max_lots` |
| **Snapback** | `snapback` | A sharp move away from a stock's own recent range that tends to snap back. Scans daily bars after the close and before the next open; an armed plan becomes an entry | Plans are armed on the Snapback screen; the day-one exposure envelope applies |
| **Gamma Unwind** | `gamma_move` | Zones and watchlists on index and stock options, with closed 5-minute and live-tick entry triggers (client 0.8.2, spec S1.6.0) | Auto-entry is off until armed; live intraday needs a broker-held stop (`stop_mode` `broker` or `both`, the default); live swing is refused; `execution_lots` defaults to 1 |

All three run together. There is no mode that suppresses one of them; to stop a
strategy from entering, leave its auto-entry unarmed, or stop everything with the
kill switch ([EXECUTION.md](EXECUTION.md)).

## What every entry goes through

The same canonical path for all three: effect classification, risk approval,
safety admission (twice), fresh broker capital evidence, the day-one exposure
envelope, the idempotent order journal. Entries are refused outside market hours
and on stale signals or data. See [EXECUTION.md](EXECUTION.md) and
[RISK_MANAGEMENT.md](RISK_MANAGEMENT.md).

## Retired strategies: maintenance only

Adaptive Edge, Intraday, Opening Volume and Navigator never originate. Their
loops keep running in maintenance mode so positions they already hold are
reconciled, protected and exited. Their screens remain readable.

Other scans and studies (OI wall flow, ATM premium imbalance, NIFTY opening-range
breakout, PCR, opening-volume leaders) are read-only analysis. They consume
normalised Indian-market candles, quotes, option chains and depth, and place no
orders.

## Expectations

Performance is not guaranteed. Backtests in this repository, including long-option
Supertrend studies that were not net-positive out of sample, are why the first days
are capped at one lot and a loss budget. Raise size only by decisions made in
advance, never after a loss.

## Strategy documentation

- Snapback: [docs/strategy/snapback/README.md](docs/strategy/snapback/README.md)
- Gamma Unwind: [docs/strategy/gamma-move/README.md](docs/strategy/gamma-move/README.md)

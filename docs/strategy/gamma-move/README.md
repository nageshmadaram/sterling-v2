# Gamma Unwind V1

**Stable strategy ID:** `gamma_move`  
**Reference package:** `GAMMA-UNWIND-client-0.8.2-linux`  
**Reference spec:** S1.6.0 · client edition 0.8.2  
**Sterling role:** signal-parity port plus a separate guarded execution adapter.

This directory documents the Gamma strategy Sterling currently ships. The older
A310 "Gamma Move" research engine has been retired and removed; it is not an
alternate implementation.

## Reference signal flow

1. Rebuild daily supply/demand zones with the reference pivot/ATR rules.
2. Build current/next-expiry stock lists. When current expiry is at or below
   seven NSE trading days, the client expands from two lists to four.
3. Keep the client edition's single highest-OI OTM contract per stock/list
   (effective **HOI-1**; the supplied YAML still records `top_n_oi: 2`).
4. On **closed 5-minute bars**, arm hidden E2 only when A+B+C+H all pass:
   - A — OI drop from the session OI peak.
   - B — volume build against SMA20 with the required last-three participation.
   - C/H — the reference consolidation/base conditions.
5. After E2, emit E1 only on the **live option tick** that breaks the base while
   the forming-volume condition passes.
6. Apply the four reference gates: **ZONE, LIQUIDITY, DIRECTION, UNWIND**.
   Missing gate inputs do not count as passes.
7. Tier exactly:
   - 4/4 = `ENTRY`
   - 3/4 = `STRONG_WATCHLIST`
   - 0–2/4 = `WATCHLIST`

The reference client is signal/alert-only. Sterling does not change those tier
rules. Only `ENTRY` is eligible for Sterling's execution adapter; the other two
tiers remain alert/dashboard states.

## Reference trade follow-up

- T1: +20% option premium.
- T2: +40% option premium.
- DTE <= 1: intraday, base-low premium stop, flatten at 15:20 IST.
- Swing: D68 underlying daily-close zone-edge stop before T1.
- At T1: move the premium stop to actual entry; no partial-book percentage is
  invented because the supplied client does not define one.
- Exit at T2, reference D68 stop, expiry constraint, or the five-trading-day
  horizon.

A live swing ENTRY remains fail-closed before T1 because the reference D68 stop
is an **underlying daily-close condition**, not a broker premium trigger. Sterling
will not pretend those are equivalent.

## User settings

The Gamma Unwind settings page exposes every configurable runtime field from the
port: zones, stock/strike bands, expiry-list threshold, OI/volume/base rules,
four gates, warnings, five-day hold, schedule, and Sterling execution controls.
Client-fixed invariants are shown but cannot be edited:

- 1-minute base tape
- 5-minute signal bars
- 15-minute display context
- 10-minute token poll
- gates on all lists
- effective HOI-1

## Runtime architecture

`app.engines.gamma_move` contains the pure reference-rule port.
`gamma_move_scanner.py` adapts Kite history/quotes/ticks into that engine.
`gamma_move_runner.py` owns durable signal/position lifecycle and the guarded
Sterling execution adapter. Kite ticker fan-out drives live E1 and open-position
T1/T2/stop handling even when no Gamma UI is connected.

Shared replay now displays dated recorded observations or historical minute-proxy
signals, with causal seeking and no fabricated fills or P&L. E1 depends on
intrabar option ticks and session OI/volume state; replaying only historical
candles cannot prove live parity. This replay is signal-only; it cannot arm orders.

## Verification

The core parity contract is pinned in
`backend/tests/unit/test_gamma_unwind_reference.py`, with scanner, lifecycle,
API, Telegram and frontend board/settings regressions alongside it.

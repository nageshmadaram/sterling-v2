# Snapback model evidence: strict-JSON restatement and current gate verdict

Date: 27 October 2026. Scope: **read-only historical model**; **not** a
production authorization, exchange fill confirmation or profitability certificate.

## Preserved original and parseable derivative

The immutable original is
`docs/evidence/2026-10-23/snapback-current-oos.json`, Git content blob
`2e0b2c66bd5e31342ea02d4d0d58a4322ee246a7`. It contains 36
non-standard `-Infinity` internal selector-score sentinels. Ordinary
`JSON.parse` refuses this file.

The additive derivative
`docs/evidence/2026-10-27/snapback-current-oos-strict-json-restatement.json`
replaces **only** those 36 non-finite `-Infinity` selector scores with JSON
`null`. No simulated fill, research outcome, candidate label, trade count,
drawdown or result was altered. The derivative was independently read back
from GitHub and successfully parsed as strict JSON. **Neither document is a
live execution receipt.** A historical `verdict.promoted=true` field is the
earlier *research* judge's answer, **not** a passed authoritative lane gate.

## Failed economic acceptance (frozen rules must not be weakened)

The 23 October research review records 198 out-of-sample **modelled** swing
trades over 77 distinct entry dates, `+₹733,264.50` simulated net
including a *modelled* hedge, and `-13.05%` portfolio drawdown. The
separately parameter-selected model has 127 OOS trades and a confidence
interval crossing zero. Source coverage was 633 historical sessions from
4 April 2024 to 23 October 2026, not the nine-year legacy claim.

The predeclared `docs/evidence/PROMOTION_POLICY.md` independently requires,
among other evidence, **at least 300 authoritative completed trades**,
**at least 60 fully observed sessions**, and **MTM drawdown at most 10%**.
Modelled OHLCV-only trades are expressly not counted as authoritative
paper/shadow/broker fills. Thus the research result is **not eligible for
economic release** even before accounting for incomplete broker fillability,
identity matching, futures hedge execution, fee/margin receipts, exposure-
matched null and date-effective instrument masters. Do not weaken those
thresholds to report success.

## Functional correction made in this branch

`walkforward.equity_series` previously derived its "full" calendar only
from trades' entry and exit days; in reality it dropped tape sessions with no
exit. The branch now passes the actual source-tape session calendar through
the in-sample and out-of-sample book summaries and the deflated Sharpe
calculation. Legacy endpoint-only outputs are labeled insufficient, and the
`judge` research promotion now fails if no full tape calendar is supplied.
Further, ineligible selector scores serialize to strict JSON `null` and
`study/snapback_research.py` uses the strict atomic, no-overwrite report
writer. The original source is preserved unchanged.

A synthetic five-session/two-exit unit example verified independently:
the summed allocation-scaled return is 4% either way, but per-counted-session
mean changes from 2.0% over two trade-endpoint observations to 0.8% across
all five observed sessions. That is a mathematical demonstration, **not** a
recomputed Snapback trading outcome.

## Verification and hard blockers

- **Verified:** strict-JSON restatement parses; source selection counts and
  recorded numbers match the original. Broker-free acceptance script
  `scripts/snapback_offline_acceptance.sh` was shell-syntax checked
  locally on an exact Git blob match (`1f9702424b07affaf1f82e46f1fbb9e3d4e7d4c5`).
- **Not verified on this SHA:** full Sterling unit/integration suites, market
  backtest after accounting fixes, simulated forward fill-quality study,
  exact account static-IP identity, actual broker order/protection/fill/exit/
  restart receipts or complete hedge after-cost P&L.
- **Decision:** Snapback LIVE certification **BLOCKED**; demonstrated
  after-cost **forward profitability NOT ESTABLISHED**. Master new real-money
  entries **OFF**. Operator may review diagnostics without broker orders.

No capital allocation or strategy threshold changed.

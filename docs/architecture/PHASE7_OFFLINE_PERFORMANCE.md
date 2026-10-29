# Sterling V2 Phase 7 — Offline hypothetical performance analytics

**Scope: offline-only analytics, no trading authority.** All Phase 1–6 PRs
remain draft and unmerged. This proposal is dependent on Phase 6 PR #8.
No existing broker execution client, order router, risk permission or live
position record is changed.

## Fundamental distinction: decision is not a fill

The offline Phase 6 journal contains decisions and queue outcomes, **not
confirmed executed trades or price/cost fills**. Therefore Phase 7 does NOT
calculate P&L directly from an approved strategy signal. Without separately
supplied entry price, exit price, quantity, fees and slippage, P&L is
**unavailable**, never assumed zero or inferred from a quotation.

The only permitted additive data source is a
`HypotheticalFillPair` with `source_provenance=operator_supplied_synthetic`
and `evidence_kind=offline_hypothetical`. It binds **two different,
completed, Phase-4-approved, Phase-6-hash-verified offline decision events**
for the same immutable strategy version, symbol, risk policy and strategy.
Quality must have been rated good in the original **offline** validation.
Inputs, source decision hashes and derived P&L are recorded as immutable
audit artifacts. A caller CANNOT label these as actual Kite/broker fills or
make them count toward real-money performance.

`OfflinePerformanceService.record_hypothetical()` is deliberately an
offline, authenticated Python service with no HTTP write endpoint or
autonomous run worker. It is not a broker adapter. The existing signed-JWT
registry verifies strategy ownership. SQLAlchemy and PostgreSQL serialize
append-only trade records with the Phase 4 advisory transaction lock.
SQLite supports isolated synthetic tests only.

## Trade journal

New additive Alembic revision `e97c602ab458` creates a separate
`platform_hypothetical_trade_events` table, with:

- strategy/version and entry/exit validation-run foreign keys
- unique entry/exit decision IDs and source audit hashes
- symbol/exchange, UTC entry/exit assumption timestamps, signal and
  risk-policy lineage
- explicit side, assumed quantity/prices, entry/exit reasons, synthetic
  market regime, fees and slippage assumptions
- gross price movement P&L, net hypothetical P&L and optional R risk unit
- actor, event timestamp, per-strategy sequence and SHA256 linked hashes
- `execution_authorized=false` enforced by Python and SQL CHECK constraint.

BEFORE UPDATE/DELETE triggers enforce append-only records on disposable
SQLite and PostgreSQL16. Source decisions and original fill assumptions
are rechecked during read operations. The ledger cannot establish that
market prices were historically obtainable, realistic or broker verified.

## KPI mathematical definitions

All amounts are INR, computed with finite `Decimal` arithmetic.
Trades are ordered by assumed **exit UTC timestamp**, then trade ID.
No signal is treated as a closed trade without a separate accepted
hypothetical pair.

- Gross profit = sum of positive **net-of-fee trade P&L**; gross loss =
  absolute sum of negative net-of-fee trade P&L. Gross *price movement*
  before costs is reported separately.
- Net P&L = sum(price movement − explicit fees − explicit cash slippage).
- Profit factor = gross profit / gross loss; **null** if there are no
  losing trades (do not report infinity).
- Win rate excludes zero outcomes from wins but includes all trades in
  denominator. Expectancy = mean net P&L per trade. Average wins/losses
  use their respective positive/negative trades.
- Drawdown is the peak-to-current decline in the chronological synthetic
  equity curve. Drawdown percentage requires explicitly provided positive
  starting equity; otherwise it is **null**. Starting equity is an
  assumption, not broker inventory.
- Return per trade is net P&L / assumed entry notional. Average R =
  net P&L / explicitly supplied risk amount; null if none supplied.
  Mean and peak *assumed single-trade entry notionals* are provided for
  comparative sizing, but `observed_live_exposure_inr=null`: these values
  are **NOT actual open position or portfolio exposure**.
- Daily/session buckets are **UTC days**, not a certified NSE/BSE session
  calendar; monthly buckets use YYYY-MM.
- Sharpe = mean / sample standard deviation × √252 on **active UTC-day**
  synthetic returns divided by specified starting equity. Sortino uses
  downside root-mean-square rather than sample SD. Less than two periods,
  no capital or zero denominator returns **null**. These are exploratory
  active-day statistics, NOT production-grade calendar/portfolio Sharpe.
  A zero risk-free reference rate is assumed.
- Rolling profit factor and rolling expectancy require a complete,
  configurable trade window, 2–200. Incomplete windows return null.
- Strategy/symbol contribution is summed net hypothetical P&L.
  Strategy-version, symbol, regime, approval-mode and date comparisons
  are all operator-scoped and never mutate original registry versions.

A small sample, unknown denominator or unavailable risk figure is
represented explicitly as unavailable; the UI/API must not present it
as evidence of profitable live trading.

## Read-only operator/admin surfaces

Strict Sterling signed JWT is required even on local development URLs.
The Phase 7 GET-only paths under
`/api/v1/offline-validation/analytics` are:

- `/strategies/{id}/dashboard` and `/summary`: signed-scoped KPIs and warnings
- `/strategies/{id}/timeline`: KPI summary including equity/rolling series
- `/strategies/{id}/comparison`: version, symbol, regime, approval grouping
- `/strategies/{id}/symbols`: symbol KPI breakdown
- `/strategies/{id}/risk`: drawdown/Sharpe/Sortino/R statistics
- `/strategies/{id}/trades`: source-linked synthetic analytical journal
- `/compare-strategies`: authenticated bounded multi-strategy comparison.

There is no POST/PUT/PATCH/DELETE route for analytics. KPIs and warnings
are informational evidence for a future Sterling operator panel; a
new visual frontend was intentionally not introduced in this scope.
Any screenshot/mobile acceptance or actual message delivery is pending.
Failure to authenticate or verify any source journal denies the query.

## Informational KPI alerts

Warning objects cover: synthetic profit factor below 1, negative
expectancy, low win rate, excess assumed drawdown, net hypothetical
underperformance and insufficient sample. The defaults for low win
rate (40%), drawdown (20%) and sample size (five trades) are **illustrative
analytical thresholds**, not risk limits or trading eligibility.
Warnings are `delivery=not_sent` with `execution_authorized=false`.
No Telegram or other external notification or position-kill behavior occurs.

## Validation and remaining blockers

CI runs Phase 7 calculator/source-linkage, rollback, API and immutable
ledger tests; Phase 1–6 safety regressions; the pinned Phase 2B direct
broker-access audit; an AST GET-only/no-broker check; and disposable
PostgreSQL16 migration, insert-failure rollback, backup/restore and
downgrade/re-upgrade.

**Production readiness is NOT established by passing these tests.**

1. No actual broker fill inventory, authenticated historical-trade import,
   exchange calendar or verified price/cost source. Historical/live
   profitability remains **unknown**.
2. Offline results are biased synthetic assumptions. No statistically
   credible out-of-sample edge, risk-adjusted validity or production
   profitability is certified; no analytics result grants order authority.
3. Privileged database INSERT/DDL could forge records or disable
   triggers; least-privilege PostgreSQL and independent WORM attestation
   are unresolved.
4. Phase 4 risk and Phase 5 queue cross-transaction atomicity remains
   unproven. Existing legacy broker access and live order authority
   remain completely unchanged and not protected by this metrics slice.
5. No production deployment, authenticated live broker identity,
   reconciliation of real fills, remote device UX acceptance or external
   notification delivery has been demonstrated.

**Keep PR #9 draft and do not merge, deploy, enable live trading or
proceed to Phase 8 without explicit safety review.**

# EMA Trend: family operator guide

Updated 2026-10-17. This release is **PAPER ONLY**. Virtual profits are not income; economic validation is inconclusive. Live execution is blocked in the server, not just hidden in the interface.

For the short handover, read [START_HERE.md](START_HERE.md). Current release boundaries are in [READINESS_REPORT.md](READINESS_REPORT.md).

## Start a supervised paper session

1. Open Connect → **10/21 EMA + Supertrend**. Read the capability/blocker messages.
2. Search and select actual broker-listed NSE/BSE cash equities or NFO/BFO futures and long CE/PE contracts. Filters cover exchange and contract type; selections are staged until Apply. Indices require an actual listed derivative. MCX currently has an explicit execution blocker; its multipliers, sessions and canonical accounting are not yet supported.
3. Set a virtual paper allocation. The initial allocation is zero, the scanner is off, and automatic execution is off. Choose a profile and apply changes. Applying settings does not place an order.
4. Open the EMA Trend signal workspace and press **Scan now**. A missing setup, holiday/history limitation, stale quote or unavailable context can correctly produce no actionable row. Do not bypass a blocker.
5. Inspect **Chart** and the row detail. An EMA crossover alone is insufficient: the strategy waits for a valid retest or consolidation breakout and bullish confirmation.
6. Press **Paper**, then **Execute paper plan** for a manual entry. The server determines shares from available virtual cash, stop distance, costs and risk limits. A request acknowledgment is not a fill. Confirm actual shares in the campaign.

| Profile | Execution candles | Completed context | Product | Holding rule |
|---|---|---|---|---|
| Swing | Daily | Weekly | CNC equities / NRML derivatives | Condition based; no arbitrary hourly timeout |
| Intraday | 15 minute | 60 minute | MIS paper units | Session exit |
| Scalp | 3 minute | 15 minute | MIS paper units | Maximum 20 bars or session exit |
| Ultrascalp | 1 minute | 5 minute | MIS paper units | Maximum 15 bars or session exit |

All profiles use EMA10/21, DEMA50 and Supertrend(10,3). Short profiles require supportive completed context and use tighter entry/risk/quote-age constraints. These adjustments are research hypotheses, not demonstrated profit improvements. Candle completion and pivot confirmation delays are intentional.

## Inspect and stop

- **Pause paper entries** stops new exposure. It does not close shares already held.
- The **PAPER exposure · all profiles** panel shows active campaigns, confirmed shares and pending/unresolved campaign counts across profile filters. **Close all EMA paper campaigns**, followed by **Confirm paper close all**, pauses new entries and requests owner-scoped closure. It reports requests, not guaranteed fills; inspect remaining shares and unresolved orders.
- Disabling the scanner or automatic entries does not disable management of existing campaigns.
- Open a held row detail to see confirmed shares, entry, stop, state and pending actions. **Reduce 25% paper** requests one base-quarter reduction; **Close paper campaign** requests closure of confirmed owned shares.
- Check that fills reduced shares and that the final state is CLOSED. Pending, UNKNOWN, RECOVERY_REQUIRED, stale-data and order-rejection states require attention. Do not repeatedly submit replacement orders when settlement is uncertain.
- Check **Supervision** in the operations panel: RUNNING is a recent successful backend cycle, DEGRADED carries reported errors, STALE means over 30 seconds since completion, and UNKNOWN means no completed check. None certifies broker protection. New buys from changed/unverified rule identities are blocked; owned sells remain possible.
- Keep the backend and market feed running during supervision. Browser closure does not stop the backend runner, but shutting down the backend or losing quotes stops its paper monitor. **PAPER_MONITOR_ONLY is not a broker-held protective stop.**
- Known terminal failed or incomplete reductions escalate to CLOSING for conservative risk closure on subsequent supervision. Inspect the order/campaign and confirmed shares. An optional successor attempt for the original reduction is not implemented; UNKNOWN orders are never blindly retried.

The strategy uses fill-based quarters: for 103 initial shares, base quarter is 25 shares and the residual tranche is 28. A partial or rejected request is not reported as fully executed. Add-backs require a valid setup, a matching vacancy, risk capacity and no averaging down. Stops only move toward lower risk.

## Save records and recover

In Connect, use **Export saved paper records** to save a JSON export. It contains saved settings, campaigns, order projections, events and replay records; it is capped at 2,000 records per collection and is not a complete database backup or a restore file. Save it privately because it contains trading records. Export does not include broker credentials.

The application's existing full SQLite backup service captures the strategy tables and canonical journals. A hermetic automated backup/restore test verifies settings, campaign shares, UNKNOWN outbox state, fill events and owner isolation. This is a software proof, not evidence that a backup of this installation already exists. The current strategy panel does not provide a full database restore button.

For installation recovery, have the maintainer use the existing checksum-verified database backup/restore procedure with the backend stopped. Preserve the existing database before replacement; restore the complete database, not individual strategy JSON records. Start with entries paused, allow canonical order reconciliation, then inspect every open/pending campaign before resuming paper entries. Do not delete UNKNOWN orders to make a warning disappear. Restoring an old database can also restore old configuration, so inspect settings again.

## Research and limitations

**Recorded replay** uses recorded bars and is read-only. The unified backtest selector also offers the strategy's native lifecycle report. These reports label OHLC fills as modelled and economic status INCONCLUSIVE. Daily next-open entry is a research proxy; it is not evidence of execution in the actual next-session entry window. One-minute OHLC cannot establish realistic ultra-scalp queue position, spread, latency or market impact.

The report’s **Recorded dataset quality** section shows the signal/context bar counts, missing-candle and other audit issues, and an expandable manifest. The manifest hash changes when recorded prices, timestamps, provider lineage or context bars change. CHECKS PASSED means only that the stated checks passed within the observed bounds; it does not certify a complete provider history, corporate actions or fill realism. Weekly constituent coverage requires separate upstream evidence and is marked PARTIAL CHECKS. Saved-record exports include scan dataset manifests.

The supported historical exchange calendar is deliberately bounded; unknown sessions fail closed. Corporate-action restatements are rejected rather than silently changing an existing campaign. Settlement, corporate-action lifecycle adjustments, live broker protection, real fee reconciliation and economic holdout/forward evidence are not accepted. Never use this implementation as a promised source of family income.

For technical evidence and unfinished acceptance items, see [implementation status](IMPLEMENTATION_STATUS.md), [conformance matrix](CONFORMANCE_MATRIX.md) and [validation report](VALIDATION_REPORT.md).

The application Operations page also shows an **EMA Trend paper operations** section. Its pause/resume and close-all controls affect only owned EMA virtual campaigns. The existing Snapback family stop retains its original scope; use the dedicated EMA paper pause for this strategy. Unavailable data disables these controls and is not displayed as zero exposure.

## Exact derivative contracts

NFO/BFO futures and long calls/puts use their own contract OHLC for every indicator and setup. This is not an automatic translation of an underlying EMA signal into an option order. Broker metadata supplies lot size, tick size and expiry. The server rejects missing/contradictory metadata, changed contract details between plan and arm, non-lot fills, expiry-day buys and non-tradable indices.

Initial derivative sizing requires at least four complete lots. Each base reduction is floor(initial lots / 4) lots, with residual lots retained for the final tranche; no action rounds risk upward. Futures reserve full notional against strategy allocation rather than treating the simulator margin percentage as free extra capital. Long options reserve premium; naked shorts are unavailable. Unexpected fill-based risk above the original budget puts the campaign into risk closure.

Expiry-day exposure is directed to risk closure; actual closure still needs current quotes and canonical execution. No contract rollover occurs. Daily/Weekly derivative profiles can remain unready because an individual listed contract lacks 250 daily and 100 completed weekly context bars. The system does not splice contracts or manufacture continuous history to bypass this. Recorded derivative replay requires frozen instrument metadata and exact contract bars.

# Start here: EMA Trend

This app contains a working, tested **paper-trading implementation** of the 10/21 EMA + Supertrend strategy in Swing, Intraday, Scalp and Ultrascalp profiles. It uses virtual money. It has not demonstrated dependable profits and is not ready to manage real family capital.

## First supervised session

1. Open Connect → **10/21 EMA + Supertrend**. Leave automatic entry off initially.
2. Use instrument search to select a broker-listed NSE/BSE cash equity or NFO/BFO future/long option contract. Choose a profile, set a virtual allocation, enable scanning and apply changes. An index itself cannot be ordered; select its listed derivative. MCX contracts are visible with an execution blocker pending commodity support.
3. Open the EMA Trend workspace. Check **Supervision** before relying on the paper monitor. RUNNING means a recent backend cycle completed without reported errors; it is not broker stop protection or a promise that a trade is available.
4. Press **Scan now**. A setup may not exist. Missing data, a holiday, stale quotes or insufficient warmup are valid reasons to wait.
5. Inspect the row and chart. **Paper → Execute paper plan** requests a server-sized virtual trade. Confirm filled shares in the campaign.

## If something needs attention

- **UNKNOWN**: no completed supervision check is recorded.
- **STALE**: the last completed cycle is over 30 seconds old.
- **DEGRADED**: a cycle reported a feed, management or reconciliation error; read the displayed reason.
- **Pause paper entries** stops new exposure while existing management continues where data and reconciliation permit.
- **Close all EMA paper campaigns → Confirm paper close all** pauses entries and requests closure across all profiles. Check remaining shares and unresolved orders afterward. Acknowledgment alone is not a fill.
- A changed/unverified rule identity blocks new buys. It does not block requesting closure of confirmed owned shares.

Keep the backend and quote feed running during supervision. Closing the browser does not stop the backend; stopping the backend stops the paper monitor. There is no certified broker-held stop for this strategy.

## Save and hand over

Use **Export saved paper records** in Connect to download settings, campaigns, order projections, events, dataset manifests and research runs. It is capped at 2,000 records per collection and is not a full database restore file. Full SQLite backup/restore is a maintainer operation using the existing application backup service; a temporary-database round trip is tested.

Read the [operator guide](OPERATOR_GUIDE.md) for the profile rules and recovery procedure. The [readiness report](READINESS_REPORT.md) states what is verified and what remains blocked. The [implementation status](IMPLEMENTATION_STATUS.md) lists remaining work against the original plan. No additional conversation with the coding agent is needed to find these instructions.

The application Operations page also shows an **EMA Trend paper operations** section. Its pause/resume and close-all controls affect only owned EMA virtual campaigns. The existing Snapback family stop retains its original scope; use the dedicated EMA paper pause for this strategy. Unavailable data disables these controls and is not displayed as zero exposure.

For derivatives, quantity is exchange units in whole broker lots. A new staged campaign requires at least four lots; reductions and add-backs never use fractional lots. Swing uses NRML, shorter profiles MIS. Futures reserve full notional conservatively; long options reserve premium. Signals come from the selected contract’s own candles. Insufficient contract history correctly blocks readiness, particularly for Daily/Weekly. There is no automatic strike substitution or rollover.

# EMA Trend readiness report

As of 2026-10-17.

**Software state: verified paper implementation. Connected-market acceptance: outstanding. Unattended/live release: blocked. Economic evidence: inconclusive.**

The requested target is now live broker execution. Shared settled-holding protection verification, connected diagnostic checks and current signal admission were added as prerequisites. The EMA live transport and protection lifecycle are still unfinished; see [live transition status](LIVE_TRANSITION_STATUS.md). Tests of these prerequisites do not change live eligibility.

Production integration follow-up: each current campaign ticket now offers **Export execution evidence**. The authenticated read-only `/api/v1/ema-trend/campaigns/{campaign_id}/evidence` endpoint links its saved campaign, plan, signal, dataset and action outbox with the shared canonical order journal. Confirmed quantity/value come from canonical order observations, never submission acknowledgement. Missing artifacts, unresolved settlement, identity mismatches and pending/inconsistent fill projections produce explicit gaps. Foreign-owner or foreign-strategy canonical records are withheld. The export does not certify broker-held protection, live execution, market-feed acceptance or profitability; records are observed sequentially and can change during reconciliation. No real-money account was activated by this follow-up.

| Capability | Result |
|---|---|
| Four frozen profile contracts | Implemented: Daily/Weekly, 15m/60m, 3m/15m, 1m/5m |
| EMA10/21, DEMA50, Supertrend10/3, causal context/pivots | Independently checked |
| Retest/consolidation entry, fill-based quarters, controlled add-backs | Implemented with shared runtime/replay core |
| Cash/stop/cost/risk sizing, no averaging down | Whole-lot derivative sizing and conservative full-notional allocation checks enforced |
| Durable ownership/CAS/outbox, canonical order/fill projection | Actual virtual exchange integration tested |
| Stops/exits while scanner/auto is off | Tested; paper monitor depends on backend and quotes |
| Uncertain orders and failed reductions | Visible reconciliation state; no blind UNKNOWN retry; known failed reductions escalate conservatively |
| Version/rule changes | Missing/mismatched originating rule identity blocks new buys; owned sells remain possible |
| Supervision status | Durable, throttled completed-cycle heartbeat; errors and stale/unknown states visible |
| Connect, shared signal rows, chart, detail actions, journal, replay | Implemented; browser contract workflows cover all four profiles on desktop/mobile |
| Dataset and artifact audit | Content hashes, bounded quality checks and preserved origin/parent lineage; legacy origins remain unverified |
| Paper pause/close-all/export, family instructions | Implemented in the strategy workspace and application Operations; owner isolation, source-specific controls and unresolved outcomes tested |
| Full database backup/restore | Existing service tested on an isolated temporary database; production backup existence/restore not asserted |
| Real provider/session acceptance | Not completed against a connected market session in this work |
| Corporate actions, settlement, real fee/protection acceptance | Outstanding |
| Holdout/forward economic edge and fast-mode microstructure | Not established |
| Real-money eligibility | Disabled in backend; cannot be enabled by the strategy settings |

The completed-cycle heartbeat is not a watchdog running outside the backend. If the backend itself is inaccessible, the UI may fail to refresh; treat a refresh failure or old displayed time as unavailable supervision. RUNNING describes the last completed cycle, not guaranteed continuous management.

The full plan is not accepted merely because automated tests pass. Remaining production and economic evidence cannot be manufactured by changing thresholds or using profitable synthetic fixtures. The current default is scanner off, automatic entry off and zero allocation for fresh configurations. Existing saved operator settings are preserved.

Start with [START_HERE.md](START_HERE.md). Reproducible commands, current counts and saved logs are in [VALIDATION_REPORT.md](VALIDATION_REPORT.md); detailed unresolved scope is in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

Instrument expansion: shared-style broker search and contract filters now cover executable NSE/BSE equities and NFO/BFO futures/long options. Canonical virtual entry/reduction/close is tested for FUT/CE/PE on both derivative venues. MCX is explicitly blocked pending correct multiplier/session/accounting support; the entire requested commodity expansion is therefore incomplete. See INSTRUMENT_EXPANSION.md.

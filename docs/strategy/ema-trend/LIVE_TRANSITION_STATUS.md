# EMA Trend live transition

The requested target is real broker execution using the existing account and shared system controls. **That transition is not complete.** The current EMA execution service still admits only the concrete virtual client, and the release allowlist still rejects EMA live entry. No real-money order, broker trigger, account activation or production restart was performed during this change.

## Implemented prerequisites

- The shared GTT inventory verifier can now prove overnight CNC holdings when a positions-only read no longer contains the equity. It does not sum overlapping holdings/positions or add T+1 quantities. It excludes used and collateral quantities, rejects missing/discrepant/ambiguous holdings and rejects fractional broker quantities. A negative same-day CNC position reduces coverage conservatively. This improves the shared broker protection primitive; **it does not yet connect EMA-owned fills to that primitive**.
- Authenticated `/api/v1/ema-trend/readiness/check` checks one saved symbol/profile against the existing active broker account. It verifies concrete client/account/profile identity, reads inventory/order/GTT books and contract metadata, checks the executable quote, and records diagnostic observations. It does not return execution authority. A successful GET does not establish owned protection or forward acceptance. Raw holdings, orders, credentials and unrelated account records are not returned in this report.
- Connect exposes the check in the existing EMA execution/evidence section. Saved reports are included in the strategy export. There is no additional broker-account setup workflow.
- Signal snapshots include current entry expiry, calendar/window/config/activation blockers and source metadata. The shared board disables stale/out-of-session entry eligibility and distinguishes broker-data signals from paper execution. This does not rename paper campaigns as live fills.

The settlement proof follows the separate positions, holdings, realised/used/collateral and authorisation fields in the [Kite portfolio contract](https://kite.trade/docs/connect/v3/portfolio/). Sale authorisation remains a separate observation. [GTT registration and child orders](https://kite.trade/docs/connect/v3/gtt/) still require reconciliation; a trigger ID is not an exit fill.

## Still required before live entry

1. Account-bound live execution transport and ownership namespace, canonical entry/exit journal and broker trade/charge projection; existing virtual campaigns must retain their own transport.
2. Durable protection placement and replacement for confirmed EMA quantity, timeout/restart recovery, and attribution of triggered child orders. MIS needs its exchange-stop lifecycle; the existing GTT primitive does not support MIS.
3. Exact quantity reconciliation across overnight delivery and partial sales, depository authorisation, corporate actions, derivative settlement classification and pre-expiry closure.
4. Connected-market acceptance of the complete entry/protect/reduce/add/exit/restart lifecycle. HTTP contract fixtures and virtual exchange tests cannot establish this.

Do not remove the live admission restriction until these components are integrated and verified. Do not describe this prerequisite work as live implementation completion.

Validation: 277 backend checks (225 shared protection/settlement + 52 EMA runtime/API), 22 frontend unit checks, 10 desktop/mobile browser scenarios and the production frontend build passed. Browser scenarios retain the existing virtual-execution fixtures; they do not prove broker execution. Saved logs are in `validation/live-transition-*.txt`.

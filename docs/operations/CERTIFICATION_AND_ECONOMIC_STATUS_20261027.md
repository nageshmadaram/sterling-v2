# Sterling certification and economic evidence — 27 October 2026

**Release:** DRAFT PR #272; do **not** merge as a certified or profitable product.  
**Master new-entry posture:** default OFF (`STERLING_LIVE_EXECUTION=false`); never toggle it from a document, CI job, or deployment.  
**Authority:** only the actual account owner, the broker, an independent reviewer, and executed evidence may advance a live release. Software changes alone cannot.

## Evidence checked and deliberately NOT promoted

Run `python3 scripts/strategy_evidence_matrix.py` to obtain a **read-only** JSON matrix with input hashes, mode-level results, historical defects, and explicit `new_live_entries_authorized: false`. This never touches Kite, credentials, a database, or release authority.

| Strategy | Recorded evidence | Finding | Present release action |
|---|---|---|---|
| SuperTrend | `supertrend_mode_sweep_2026-10-22.json` | Five selected mode configurations rejected; recorded selected out-of-sample totals are negative in all five modes (ultra -29.13%, scalp -3.67%, intraday -4.10%, overnight -3.31%, swing -5.38%). | Do not promote any recorded configuration |
| EMA Trend | `ema_modes_backtest_2026-10-22_fee3bps.json` | Overnight net -INR 15,430.9/25 trades; intraday -INR 1,545.4/1 trade; other modes zero trades. | Research only |
| Gamma Unwind | `gamma_modes_backtest_2026-10-21.json` | 15 session records incomplete; only one underlying signal. A positive one-trade mode is not statistical evidence. | Repair historical tape and reconstruct date-effective option masters |
| Snapback | `snapback_timeframes_backtest_2026-10-22.json` | Ultra/scalp reported positive aggregate results from a narrow subset; 3 contract-processing exceptions in each mode. Daily swing results are modelled, not historical traded options. | Repair errors, rerun frozen independent OOS and forward replay |

**No historical report here establishes after-cost expected live profit**; all candidates remain without a verified independent G7 economic gate.

## Executed versus unexecuted testing

- Operator reported **108 focused tests passed** in 1.66 seconds on source SHA `c83f44b0`. This covered release authority, acceptance evidence, CI contract, research coverage, and SuperTrend refresh.
- Later scanner, research-data and economic-validator changes are **not covered by that run**. Rerun the relevant exact-SHA suites when an authorized host/runner is available.
- GitHub Actions jobs on prior PR heads reported failure with `runner_id=0` and **zero executed steps**. Those conclusions do **not** constitute test execution proof. Investigate GitHub billing/runner eligibility and job annotations, but do not label the cause until verified.

## Remaining mandatory go/no-go gates

1. **Immutable release identity (G0, G1):** clean exact SHA; named deployment artifact, reproducible dependencies, executed CI and full safety suites on the same SHA. A skipped/unstarted job is UNKNOWN.
2. **Host, recoverability and alerts (G2, G5, G6, G8):** independent persistent storage with headroom and adequate IOPS; restore of databases, intent journals, safety state and lane manifests; crash/reconnect drills; verified operator-device alerts; named authorized handoff.
3. **Broker eligibility (G3):** Kite login, exact account ownership and capital, static egress **registered and matching** the actual outbound IP; callback authentication, fresh order/position inventory, rate limiting and API mode/variety compatibility. No copied family account authorization.
4. **Real order lifecycle (G4, G9):** under separate explicit account-owner permission and suitably controlled risk, independently observe entry submission, partial/full fill, broker-side protection, trailing, exit, stop trigger, order acknowledgement/reconciliation, restart and unknown-submission handling. An API order ID is not a fill.
5. **Economic acceptance (G7):** preregister strategy+mode+config and frozen data identity; build actual real-option data coverage; run chronological purged OOS and forward evidence; include real charges, spread, impact, missed fills, expiry, turnover and worst-case stop slippage; require positive independently reproduced after-cost expectancy and lower confidence bound, plus drawdown/stress limits. **Never invent trades or certify from self-reported booleans.**
6. **Operator deployment authorization:** independent sign-off and explicit bounded rollout decision separate from all code and CI. Keep master new-entry halt OFF until then.

### India regulatory/broker facts requiring fresh operator confirmation

- Zerodha states that API-based orders require a registered static IP, effective **1 April 2026**, and other read-only/market-data endpoints can operate from different IPs: https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip
- Zerodha documents **10 orders per second per client account** as a threshold and additional usage conditions: https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs
- NSE/SEBI retail algorithmic order-tagging, approval, API and family restrictions must be independently checked for the account's exact category and strategy before rollout; a software test is not regulatory permission.

## Current hold conditions

- **Technical R1:** not independently certified on a running approved production host.
- **Broker R2:** not independently certified; live fill and protection receipts absent.
- **Economic R3:** not independently validated. Historical positives, where present, are **not** evidence of expected live earnings.
- **New autonomous real-money entries:** remain OFF.
- **Operator availability:** the principal operator is currently unavailable to perform on-host or broker-account steps. Do not substitute fabricated evidence or initiate unattended live orders.

## Release handoff

Keep PR #272 as a review branch until CI genuinely executes and the above evidence is available. Preserve all local journals, deployment secret material, current broker inventory, and backups. The workstation was last observed at **99% root disk usage with 1.8 GiB free**; diagnose storage non-destructively before attempting large builds, Docker image pruning, or backup deletion.

Nothing in this document authorizes new real-money trading. Reviewers can continue offline code, recovery, and research work while the owner is unavailable.

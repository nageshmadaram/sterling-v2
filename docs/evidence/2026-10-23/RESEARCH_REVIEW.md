# Strategy evidence review

Research observations only. No production promotion record was written and no strategy, AUTO, capital, lot, stop or risk setting was changed. The operator explicitly requested keeping trading enabled.

## Snapback configured swing model

A read-only SQLite connection copied **only daily OHLCV** into a disposable data-only database. The source has 128,437 daily rows across 204 names. The configured F&O universe supplied 200 usable instruments, 633 sessions, **2024-04-04 through 2026-10-23**. This run does not reproduce the repository's older nine-year dataset claim. Exact parameters, data digest, code digests and test settings are in `source-manifest.json`.

The configured index-futures hedge and all signal/exit parameters were retained. Research sizing was explicitly changed to **one lot**; this was a process-local model input, not an account setting. The existing walk-forward harness used six folds, 200 in-sample sessions, 60 out-of-sample sessions and its configured purge. Random timing used 300 rounds with seed 5.

| Fixed-rule model observation | Result |
|---|---:|
| OOS modelled trades / distinct entry days | 198 / 77 |
| Net modelled P&L, after model costs and hedge | ₹733,264.50 |
| Mean entry-day premium return | +11.875% |
| Reported day-clustered 95% interval | +1.24% to +23.77% |
| Reported OOS random-timing p-value | 0.0033 |
| Model portfolio maximum drawdown | −13.05% |
| Trials used by the harness | 72 |

The return percentage is the harness's entry-day premium-return statistic, not a daily account return. The separate parameter-selected OOS book has 127 trades and a confidence interval crossing zero. Raw results are in `snapback-current-oos.json`; preserve all folds and the weaker selected-book result.

**Item 18 is not a live evidence pass.** The raw harness's `verdict.promoted=true` is its research gate, not an execution authorization. The repository's stricter `PROMOTION_POLICY.md` requires at least 300 authoritative completed trades and maximum drawdown no greater than 10%, among other checks. This model has 198 simulated OOS trades and 13.05% drawdown, with no observed option fills. Its random null preserves candidate symbol/side counts and regime eligibility; matching the actual realised portfolio's filled trades, capital exposure and holding time has not been verified. Parameters' earlier calibration lineage does not establish that these historical OOS dates were untouched during prior strategy selection. Current-universe selection also does not establish historical constituent availability. These are explicit methodological/data gaps, not p-values that can be filled in by assumption.

A directional control uses the identical stored data and parameters, changes only the process-local hedge mode to `none`, and is recorded separately. It returned 198 modelled OOS trades, net ₹1,681,234.61, reported interval −0.88% to +52.80%, drawdown 17.5%, and a failed promotion verdict (deflated Sharpe/year consistency/interval). It cannot validate the configured hedged lane or another trading mode.

## All strategy/mode coverage

| Strategy / modes | What is established | Missing evidence and exact next work |
|---|---|---|
| SuperTrend: ultra scalping, scalping, intraday, overnight, swing | Canonical entry/exit code and offline execution contracts are covered by the QA suites. | The configured external executable-futures lake is absent on this host. Supply dated, contract-specific option/futures bars, fees and roll/expiry metadata; freeze each mode's config and an untouched holdout; compare with a null matched to actual filled count, side, contract, capital risk and duration. Spot-only profitability is not option execution evidence. |
| Gamma Unwind: all five trade modes | Recovery/scan logic and mode lifecycle regressions; historical replay uses its declared OI proxy. | No accepted multi-session expired-option/OI/tick dataset with independent holdout and equal-exposure random timing was established. The recent-current-contract backtest is not this test. Collect the dated instrument master plus OI/option bars and execution observations; replay the canonical engine per mode, record unavailable contracts, then compare matched exposure. Do not turn WATCH into ENTRY to manufacture a sample. |
| Snapback swing, configured index-futures hedge | The model research above; separate directional control. | Authoritative option/hedge fills, contemporaneous quotes/margins, historical universe, independent parameter lineage, and verified equal realised-exposure null. Preserve failed or missing gates. |
| Snapback ultra scalping, scalp, intraday, overnight | Separate mode implementations and offline lifecycle tests exist. | Stored daily bars cannot validate these intraday option modes. Build synchronized real option/underlying tapes with session coverage; use each mode's own candle/hold/exit contract and frozen holdout. No swing verdict carries over. |
| Price Rules: manually configured rules and products | Offline request, ownership, SL/TSL/exit and journal contracts in the original QA. | A user rule is its own experiment. Freeze each rule/contract/quantity/stop/target identity before defining a holdout and an exposure-matched random baseline. No universal Price Rules edge is inferred from another strategy. |
| EMA Trend / Adaptive Edge | Practice-only presentation and access boundaries; no new live-entry authorization. | Their practice observations do not promote any live strategy. The historical report's specific 38-name scan failure still needs a fresh per-symbol reproduction/coverage report before being declared fixed. |

## Quiet-period check

`recent_signals(..., sessions=30)` was evaluated using the configured swing settings and the isolated daily-only snapshot. It returned **30 historical modelled candidates**, latest on 19 October 2026. The covered-day list is retained in `snapback-30-session-history.json`. This is not a finding of zero signals for 30 sessions, and is not a count of actual fills or fully observed live scans. No stretch threshold or market filter was changed.

The weekly read-only follow-up is saved as `sterling-weekly-snapback-history-check`, Fridays at 16:00 in the app's local schedule. Missing data/authentication is reported as unavailable, never as a quiet market. The separate `sterling-market-session-acceptance` follow-up checks actual exchange days after the session and leaves restart/signal observations pending until real evidence exists. Both remain quiet on unchanged non-actionable states and cannot change trading controls or broker orders.

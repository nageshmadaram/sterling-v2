# Default tags and effective mode configuration

The settings and signal workspace now keep the original default visible separately from the selected configuration. SuperTrend Swing is original/default **60-minute Heikin-Ashi**, while Ultra scalp uses **1-minute Heikin-Ashi** with the same three SuperTrends. Snapback Swing is original/default **daily regular OHLC**; the other modes use their own completed option-candle reversal rules. EMA and Gamma remain original-only.

The comparison shows preset candle sizes, permitted overrides and holding limits for every supported mode. The effective readout uses the displayed settings draft or cached server configuration, including a custom candle interval, stop/target/trail and bar limits. Dormant Snapback minute settings are not presented as daily Swing inputs. Settings still require Apply; these descriptions perform no writes. Board filters explicitly identify themselves as view filters and do not change strategy configuration. Existing positions retain their frozen policy.

SuperTrend's stale hourly entry and warmup descriptions now follow the selected interval. No strategy mathematics, runtime trading control or saved account configuration changes are included. Gamma snapshot provenance text now follows the archived resolution, independent of current trigger settings. Non-session archive metadata and labels follow the existing Gamma reference calendar without changing it or deleting evidence.

## Verification

Eight mode-component tests first failed on the missing component, then passed. They cover original/default versus selected mode, the 1-minute HA disclosure, custom Scalp interval, dormant Snapback minute settings, custom Snapback management, original-only EMA/Gamma and missing configuration. The combined focused UI run passes 37 tests. Four new backend provenance tests pass. Final verification passed: **11782 passed, 30 skipped in 991.87s (0:16:31)**, **2,341 frontend tests across 251 files**, TypeScript and `VERIFY OK`. Baseline/head failure sets are empty; the exact skip list is unchanged. Final source hashes and log digests are in `validation.json`. Initial and intermediate runs are retained separately and are not counted as release evidence.

An intermediate full backend run passed but its combined gate then failed TypeScript because a new test passed an unsupported `exact` option to Testing Library. That test-only option was removed; a separate TypeScript check passed. The failed gate and subsequent complete reruns are retained; only the final successful combined gate is release evidence.

The fixed-height workspace fixture also reproduced an expansion bug before release: an open comparison grew to 697px inside a 400px workspace and squeezed the signal table to 0px. Board mode facts now use a keyboard-focusable, scrollable region capped at 40% of the workspace; detailed configuration is inside the comparison. The same fixture now leaves 221px for the table (mode region 159px, scroll content 731px). Settings keep their full explanation. The layout proof and before/after geometry are retained in `bounded-layout-proof.json`; final frontend verification includes this fix.

## Continued QA

The production Gamma History panel and date selector were successfully exercised on release e3c9e064. Selecting 22 October showed its recorded E1 and disabled historical entry controls. Evidence: `.runtime/qa/2026-10-24/gamma-history-status/deployed-history-panel.png`, `deployed-history-selected-day.png` and `deployed-history-selected-day-ax.txt`. Earlier tab timeout observations remain retained; this subsequent successful proof supersedes the unverified interaction status only. A real failed broker historical request was not deliberately induced.

Historical provenance fixes in this patch: the warning distinguishes saved minute-high/end-of-minute-volume proxies, five-minute close proxies and unverified legacy resolutions. It names intraminute ordering uncertainty and never claims execution proof. Weekend/holiday archives receive explicit calendar warnings and retain their rows; the history selector/timeline label them Non-session archive. Four new backend tests and one UI test first failed on these exact gaps, then passed. The original Gamma calendar and trading rules are unchanged.

Market-session load/recovery, actual notification receipt, authoritative option/OI/fill evidence, independent holdout and Snapback promotion remain unaccepted. Hosted CI billing is external; temporary tunnel stability remains unaccepted. No broker orders or GTT mutations were made.

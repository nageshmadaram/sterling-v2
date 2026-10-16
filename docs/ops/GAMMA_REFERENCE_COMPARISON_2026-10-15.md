# Gamma comparison and shared table repair — 15 October 2026

The supplied discussion-board screenshot is strategy evidence, not a Sterling design target. Sterling keeps its own shared table and existing theme tokens. The mobile renderer now has one canonical table scope: flat dividers, aligned entry/LTP/time/trade columns, consistent labels/value fonts and compact parent rows. Expanded evidence uses the same cell styling. Conflicting Gamma wrapper padding was removed.

## Verified strategy defect and correction

Scheduled builds replaced the live runtime, resetting already watched contracts and their E2/OI state. Historical replay followed a watch only to the next build; a name leaving the next selection stopped being monitored. This conflicts with additive watchlist updates and continuous monitoring of shortlisted options.

Same-day live runtime/HOI selections now survive later builds. Newly selected stock/list pairs are added. A new trading day drops old runtimes, and explicit configuration invalidation still clears them. Historical watches are followed from their first selection through the session close. Restart recovery reconstructs all already-due watchlist slots, retaining each pair's original contracts. No entry threshold or risk/protection gate was weakened.

## Supplied October 14–15 evidence

The saved October 14 record before this check had zero watches/signals and recorded broker-authentication failures. No saved October 15 daily record was found in that read-only database inspection. Neither is proof of zero market signals.

Updated 16 October: a read-only October 14 reconstruction restricted to INDIGO and POWERINDIA now reproduces POWERINDIA OCT 30000 PE as NEAR ENTRY at 12:00 IST, price 605.00, and INDIGO OCT 4800 PE as ENTRY at 15:15 IST. INDIGO OI drop 31.538% and volume ratio 1.269 match the reference's rounded 31.5% and 1.3x. Its historical closed-candle price is 150.85 versus the reference alert's 151.00. Historical output explicitly identifies this as a candle proxy; live evaluation retains the observed quote without rounding it to the reference.

Two further defects explained the earlier mismatch. The prior engine armed on a qualifying five-minute bar and then required another premium breakout; the updated default emits on the qualifying completed five-minute bar. The legacy live-breakout mode remains available in configuration. The configured underlying direction mode `any` incorrectly required only the short-term condition because of an undeclared fallback flag; it now implements the documented OR of its two direction conditions. The stricter `all` mode still requires both. Four checks, liquidity, minimum OI unwinding and execution protection remain enforced. This interpretation is supported by the supplied signal time and gate values, but does not certify equivalence to unavailable reference source/configuration.

Rule fixtures verify the October 14–15 seven-trading-day expiry rollover (27 October to 23 November for BREAKDOWN/BREAKOUT, retaining October EXPIRY lists), INDIGO T1 181.20 and T2 211.40 from entry 151, and PE stock-stop arming below 4731.8 without exiting until a subsequent close above that edge. These are rule checks, not a certification that the live INDIGO trade occurred or its follow-up alert matched.

The October 15 reconstruction for the 14 named stocks returned 41 watches and zero signals, but explicitly reports incomplete option candle/OI coverage, including DIVISLAB and ETERNAL. The dated instrument master is unavailable. This partial sample cannot certify the reference day summary or full watchlist parity.

The original reference client version/config, full dated instrument master, zone/option OI snapshots and live tick/volume inputs are unavailable. Alert text omits most of the full watchlist. Those are exact-parity blockers; importing the expected output or weakening filters would not resolve them honestly.

## Validation

165 Gamma historical/scanner/runner/history/alert/reference tests passed, including new same-day retention and full-session observation regressions. 154 affected frontend board tests passed. Production frontend build passed. Desktop and 390px mobile inspection confirmed shared control styling, aligned rows and no horizontal document overflow. Source dropdown placement was checked and mobile Back dismissed it. Shared dropdowns and column controls use the mobile overlay history, with consistent sizing, fonts and theme colors. No funded order was submitted. Before controlled API activation: zero nonzero broker positions, unresolved orders or active GTTs; replay idle with zero trades/signals.

Read-only comparison artifacts remain local under .runtime/audits/gamma-reference-2026-10-15 and are not published as generated live signals.

API health returned OK after activation. Existing saved history was preserved; the diagnostic reconstruction was kept separately and did not replace the day ledger.

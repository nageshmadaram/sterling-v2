# Gamma historical access and request status

Baseline `78064018f6e7c1a387dd8b60fcac9459bab39ac9`. This continues the full trading QA without changing Gamma's original signal/exit rules, modes, live/AUTO controls or broker execution paths.

## Reproduced and corrected

- **History controls were unreachable.** `StrategyBoardFooter` returned its text-only status before its existing options panel. Gamma's day picker, date input, timeline, coverage and E1 ledger therefore had no reachable UI. A compact History button now opens those existing controls. Other strategies retain their compact presentation; this does not add EMA/Gamma trading modes or restore obsolete per-strategy scan controls.
- **A ready day appeared to be loading indefinitely.** A pending backfill for other sessions displayed the same generic text as the selected-day load. Background batches now say `Loading recent trading days in the background…`; an individual pending request names its actual requested date, even if the operator has since selected a different day.
- **Failures were not scoped or recoverable in place.** A selected-day load failure now identifies that day and offers an explicit retry using the existing historical endpoint and default cache policy. It cannot retry another selected date or start an automatic retry loop. Another day's old error does not appear as the current day's failure.
- **Recovery controls could be stripped with interactive footer content.** Explicit alert content, including the retry button, remains reachable after the History panel closes. The retry button uses theme colors in the portal as well as on the board.

History remains read-only. Selecting a date, returning to the current session and retrying history do not arm a signal or send an order. Replay date controls remain disabled and replay does not initiate live historical backfill.

The panel reuses the existing responsive layout, focus-trap/scroll-lock primitives and mobile Back layer. Escape closes it and restores focus to the History trigger. The first regression run failed because the date controls did not exist in the rendered UI. Restoring access alone left four request-status/retry tests failing; the complete behavior fix passed all 28 Gamma board tests, including seven new regressions.

## Visual and verification boundary

An isolated, real-component fixture was bundled with the app's stylesheet and theme tokens, mocked Gamma data and HTTP disabled. Browser screenshots verify the history picker, dated timeout and retry at a 390 × 660 iframe viewport in light and dark themes. This is offline component proof, not an authenticated production failure/recovery test. A background-work fixture verifies the distinct label. A final retry-button theme adjustment was followed by a fresh full frontend run; the earlier frontend pass is retained separately. One subsequent run printed all tests passed but its runner returned termination code 143 before recording clean completion; that run is retained as terminated evidence and is not a release pass.

Final complete verification passed: **11778 passed, 30 skipped in 867.07s (0:14:27)**, **2,332 frontend tests across 250 files**, TypeScript and `VERIFY OK`. Baseline and head failure sets are empty and the exact skip list is unchanged; final frontend source hashes match. See `validation.json`. The backend source is unchanged; its complete gate runs alongside the frontend suite, and the TypeScript stage checks the final UI source. Deployment identity, production browser observation, configuration preservation and broker reads are recorded separately after integration.

The prior live-market, Gamma midday recovery, real Snapback restoration, notification receipt, authoritative research/fill/holdout evidence, public endpoint stability and hosted-CI billing requirements remain pending. No research promotion, frozen settings change, broker order or GTT mutation is part of this patch.

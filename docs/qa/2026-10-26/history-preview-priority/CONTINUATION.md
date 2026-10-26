# Historical-read contention follow-through — 26 October 2026

Base/deployed diagnostic release: `3aa677380d0cc8138d56b404b5358884495ece39`, PR #269. The operator requested immediate live-market work rather than deferral.

## Actual measured cause

Fresh live Gamma recovery completed at 12:09:28 IST with 116 watched contracts and no issue. Scan wall time was 219.49 seconds; completion was 241.618 seconds after observed worker start. The 180-second requirement is still failed.

Across its option and underlying reads, shared historical-limiter queue waits totalled 253.255 cumulative seconds, while HTTP requests totalled only 8.751 cumulative seconds. Cache read/merge/write totalled about 11.1 cumulative seconds and local pacing about 23.3 seconds. Overlapping read durations are not additive wall time. This identifies queue contention, not slow broker HTTP, as the dominant measured history cost in this observation.

Fresh simulation completed at 12:11:59 IST: 140 watched contracts, no issue, 144.01-second scan, 393.019 seconds after worker start. The longer end-to-end interval is retained separately from scan duration. This is not a full-day simulation pass.

## Fix

SuperTrend's nonselected-mode preview scanner already writes separate `|view:` snapshots and has no order callback. However, its broker reads still used foreground priority. Scope the existing `BACKGROUND_CALLS` flag only around that view-only operation, and reset its ContextVar token in `finally` on success, ordinary failure and cancellation.

The existing limiter's bounded background-yield behavior now applies to these preview reads. Its rate, burst, maximum yield, starvation bound and reservation algorithm are unchanged. Selected-mode scans and independent concurrent live tasks keep their original priority. Existing protection, reconciliation and exit maintenance runs before the preview and is outside this scope. Preview strategy rules, per-mode candle sizes, saved settings, request arguments and preview rotation remain unchanged. No preview is turned off and no order is submitted by this change.

This is a concrete priority defect found during the queue investigation. The measurement does not attribute every queued second specifically to previews; selected live scans also share the same budget. A new actual recovery measurement is required before claiming the timing target is met.

## Validation

Six focused preview cases reproduce five failures and one pass on the base implementation. With the fix, all six pass, alongside nine existing shared-rate-limiter tests (15 passed). Coverage includes inherited scope, real bounded-yield behavior, exceptions/cancellation, concurrent foreground task isolation, unchanged request budget and nonselected/no-order preview identity.

Final `make verify` passed: 11,858 backend cases, 30 unchanged skips, TypeScript and VERIFY OK. Full frontend: 2,402 passed across 258 files. Baseline and head failure sets are empty; the exact skip set and frozen source/log hashes are in `validation.json`. Release observations are recorded separately after deployment. The expanded live walkthrough also reproduced a frontend contract-disclosure collision, described below. The new full frontend run passed 2,402 cases across 258 files; source and log hashes accompany the final validation.

## Other work completed now

PR #269 fixed the one-hour offset incorrectly applied to short-mode SuperTrend timestamps. Browser revalidation parsed 114 displayed signal timestamps successfully and found none over one minute ahead of the live clock. Original Swing/default/selected labels remain intact. Live execution, four strategy configurations and push-state fingerprints were preserved during its guarded deployment.

The deferred Snapback 30-session review was executed immediately on a network-blocked data-only snapshot: 30 modelled candidates, complete expected coverage for 200/201 symbols, ICICIPRULI missing 23 October. Genuine saved Snapback rows remain zero, so populated restoration cannot be claimed. Research configuration differs from the frozen experiment, dataset/experiment identity is unset, evidence metadata/outcomes are absent; no provenance or promotion was fabricated. Temporary public-origin probes remain intermittent. Hosted CI jobs did not start because of owner billing/spending limits; no hosted pass or admin override is claimed.

## Controlled contention fixture

A separate socket-blocked process compared the base preview function and candidate against the actual reservation limiter and mocked HTTP transport. Both variants completed 12 foreground historical GETs and 20 preview historical GETs, with two foreground workers, four preview workers, rate 2.8 and burst 2. No broker was called. The limiter envelope assertion passed in both variants and the caller's foreground scope was restored.

Foreground completion: baseline 10.71698 seconds, candidate 4.28834 seconds. All-work completion: baseline 10.71699 seconds, candidate 10.71727 seconds. The fixed variant changes who waits while preserving completed work and the request budget; it does not increase aggregate throughput. This is one controlled fixture, not an actual broker recovery measurement or a guarantee about contention from other selected live strategies. Private dated source/results are retained under `.runtime/qa/2026-10-26/history-preview-priority/`.

## Contract-disclosure collision found during the wider live walkthrough

On release 3aa67738, expanding one eligible BHARTIARTL NOV 1920 CE plan expanded three panels belonging to different mode/time occurrences. Child row IDs included only engine, underlying, option symbol and child-array index; the same contract repeated across modes therefore shared the expansion key and calculator ownership.

Child identity now also carries source, mode, candle size, exchange and signal occurrence. Unchanged quote/entry-premium refreshes retain the same identity. Four separation cases reproduced four failures on the original implementation; those cases plus the stability control pass. Together with the existing timestamp file, 21 focused cases passed, followed by the full 2,402-case / 258-file frontend pass. Browser revalidation is required after deployment.

An initial full frontend command was launched after a working-directory error prevented the intended edit. That run was stopped (exit 143), preserved as partial evidence and not treated as passing. The corrected final source received the complete successful run above.

## Additional confirmed gaps retained for explicit follow-through

- **P2, selected-mode board continuity during scan:** scanner.py `_flush` publishes accumulated rows plus active confluence/out-of-universe rows, but omits not-yet-processed ordinary groups. The live board temporarily lost original Swing plans during a long background scan. Preserve per-source/underlying/mode/timeframe prior groups until their refresh completes, label pending/failed groups as last-known and block their new entries on both server and ticket paths. Only a successful completed group may authoritatively clear old rows. Test multi-symbol progress, empty-success versus read failure, source/mode switches, cancellation and owned-position maintenance. Do not simply re-expose stale rows as tradable.
- **P2, loaded rows versus scan coverage:** AdaptiveEdgeRightSidebar builds `scanned` from each board-array length, which EngineToolbar describes as scanned. A zero-signal Snapback board can therefore announce zero scanned despite its 200-instrument status, while EMA's retained 200 rows differ from current 15-result completed jobs. Rename this count to loaded records, and obtain actual coverage from scanner diagnostics/jobs; distinguish active/view-only rows from executable plans. Test empty-signal/full-coverage and retained-history/partial-job examples.
- **P2, PCR forming-slot semantics:** buildGrid intentionally projects the latest print into the upcoming 15-minute slot (`live: true`); e.g. 12:05 observation shown under 12:15. It currently communicates the live bucket mainly through color/data-live. Show explicit forming/until text in tile, grid and tape, keep the actual observation time, and make the same distinction accessible without color. Test forming, boundary, closed-market and stale-provider cases. This is a disclosure issue; it is not evidence of future broker prices or future-data strategy execution.

These findings are not marked fixed by the preview-priority/disclosure-identity patch. The broader live-market matrix and operational blockers remain documented separately.

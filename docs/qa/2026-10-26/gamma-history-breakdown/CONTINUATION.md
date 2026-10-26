# Live-market follow-through — 26 October 2026

Base: `99149779d9f235cc786ae0cf7abec32a552c50b4`. The operator requested that deferred work be done during today's live market.

## Changes

Gamma warmup now publishes `scan.prime_timing.history_detail`, split into option and underlying reads. Caller-owned aggregates follow each async read through a ContextVar that resets on errors/cancellation. Cache hit/miss/refresh/bypass counts, cache read/merge/write, local pacing, retry backoff, shared historical limiter queue, client acquisition, HTTP request and response decode are distinguished. Failed attempts count too. No account IDs, tokens, URLs, candle payloads or credentials are included.

Durations are cumulative across logical reads; the two option requests can overlap, so their sums are **not wall time** and must not be added to batch wall time. HTTP duration includes connection-pool, transport, server and body wait; it is not a network-only measurement. Global queue timing includes existing simulation yielding. No request limits, concurrency, retry count, strategy rules, automatic controls, sizing or broker mutation paths changed. Diagnostics in Kite's request boundary are enabled only for historical GETs within a scoped warmup read. Cache hit counts are logical cache events, HTTP attempts are actual calls to the client's request method.

Live UI inspection found a separate defect: `signalMs` added one hour regardless of a row's timeframe. A one-minute bar at 11:09 could consequently display 12:09. The board now uses the row's declared `signal_timeframe_minutes` for parent and leg timestamps, preserving the legacy 60-minute fallback, Navigator event times, replay timestamps and existing final hourly-bar truncation. Entry/exit rules and chart data remain unchanged.

## Actual live observations before this change

At 11:36 IST, official broker GET profile/positions/orders/GTT all returned 200; no net positions, orders or GTTs. No broker mutations. Saved live/strategy controls unchanged.

The running release's 32-minute monitor window had 65 healthy samples, maximum gap 30.000345 seconds and maximum health latency 0.694 seconds. Its process log showed no traceback, ASGI error, event-loop stall or incomplete Gamma recovery in this window. The first public route probe passed. A later 45.5-minute local window had 92 healthy samples and no health failures, while its public route probe failed with TELEGRAM_TIMEOUT. Both observations are retained; temporary-origin stability remains unaccepted.

Saved live watchlist build times are 09:20 and 13:15, while simulation uses the original default schedule with additional builds. Those saved settings were not altered.

Gamma: 116 live watched contracts, `scheduled_history`, issue empty; measured recovery scan 209.45 seconds and completion 226.007 seconds after worker start. History phases total about 200.48 seconds, engine replay 8.87 seconds. The 180-second recovery criterion remains failed. Simulation's later 11:15 build retained 93 contracts, replayed 47, completed its scan in 31.01 seconds and reached 140 watched contracts; this is not a full-day simulation pass.

UI: original SuperTrend Swing/60-minute Heikin-Ashi is labelled default and selected; other modes are view-only. Closed/stopped/unselected-mode entry controls were disabled. Gamma clearly explains completed five-minute E2 bars and tick-driven E1, with Watching rows disabled. Snapback default Swing/Daily regular OHLC was readable, but its genuine saved row count remains zero. EMA explicitly states Daily/Weekly, original only and practice only, with no real-order control. No AUTO setting changed or order submitted.

## Deferred history review executed now

A data-only snapshot was created from a read-only source connection: only the `ohlcv` table's 128,637 daily rows, no account/execution tables. Network connections were blocked for the calculation; the snapshot was then opened query-only. The existing `recent_signals(..., sessions=30)` model was run using the actual saved Snapback settings, without changing production settings.

Result: 30 modelled historical candidates; 200 of 201 selected symbols have all 30 expected completed NFO sessions through 23 October. ICICIPRULI lacks 23 October despite having 632 stored daily bars. This disproves neither live signal delivery nor live profitability; it does not establish a complete-universe 30-session silence and does not justify loosening filters. No synthetic live rows, fills, authoritative dataset start or strategy promotion was created.

## Validation and remaining acceptance

Focused history/cache/pacer tests: 32 passed before the full gate; the scoped collector's first run failed on its absent module, then passed. Mode timestamp regression reproduced four failures against the original calculation; all 16 cases in the file pass with the fix.

Full final `make verify` passed: 11,852 backend cases, 30 unchanged skips, TypeScript and VERIFY OK. The full frontend run passed 2,397 cases across 257 files. Baseline/head failure sets are empty; the five-case backend count increase is four new timing tests plus the automatically generated source-compile case for the new module. Exact source/log hashes and the skip-set comparison are in `validation.json`. Release/rollout observations are recorded separately after deployment. No current claim of full go-live acceptance. Full-session same-release coverage, two actual consecutive trading-day recoveries, full-day simulation, populated genuine Snapback restoration, wider device/account permutations, authoritative research lineage and owner-resolved hosted-CI billing remain separate criteria.

Research identity audit at 11:47 IST: saved config hash `9d4f327ca2905673` differs from frozen `6ecbeb53e9768a91`; runtime dataset start and experiment ID are absent. The read-only warehouse has no evidence_meta row, one opportunity, zero paper positions and zero outcomes. The trial-registry file hash verifies, but its own manifest still labels the search history a declarative scaffold; the hash alone does not prove the trial lineage or promotion gates. No historical provenance was invented.

# SuperTrend marker ownership and refresh fixes

Trading/test baseline: `2d00474b323864858b9f8d9103216014d0354d92` (PR #258). PR base: `dev` at `7be6aaf1c8c089db0ff46f7bb27b0c5f06bab8de`; the operator concurrently committed the previously dirty IPTV page there. The feature branch fast-forwarded to retain that commit. Its only changed path is the unrelated standalone IPTV HTML, with no trading/test inputs altered; frozen source hashes remain checked against the running full verification.

## Reproduced defects and exact fixes

| Defect | Reproduction / impact | Fix |
|---|---|---|
| Clearing and republishing on each clock refresh | Three unchanged signals produced six marker-store notifications; retained rows disappeared in intermediate maps. Every clear/publish cloned and flattened the map again. | Build one board-owned batch and atomically replace its previous rows. Identical entries are a no-op. Real changes flatten once. |
| Simultaneous mode/timeframe collision | Same contract and timestamp in Scalp 1m, Scalp 3m and Swing occupied one slot. | Include normalized mode and signal timeframe in each marker identity. |
| Board instance collision | Mount two boards showing the same signal; unmount the first; the second board's watchlist/ticker markers vanished. | Prefix keys with the board's React owner ID; unmount clears only its own keys. |
| Unchanged marker object churn | Updating ownership rebuilt aggregate marker objects even if the displayed flags stayed identical. | Retain individual marker references and the aggregate map when flags are unchanged; retain OR merging across publishers. |
| Hourly text in non-hourly modes | Ultra Scalp with earlier-session signals reported “No new 1H SuperTrend transitions today.” | Use mode-neutral “No new SuperTrend transitions today.” Effective timeframe remains in mode information. |

Product paths: `frontend/src/store/useSignalMarkers.ts` and `frontend/src/components/kite/SuperTrendSharedBoard.tsx`. The existing publish/clear interface is retained for other consumers.

## Verification and browser evidence

TDD: all seven initial regression cases failed against the original implementation; the extra non-hourly notice case failed independently. Final focused group: **25 passed**, including the existing 17 signal-plan cases. Final TypeScript check passed before the full gate. Final verification passed: **11782 passed, 30 skipped in 819.30s (0:13:39)**, **2,349 frontend tests across 253 files**, TypeScript and `VERIFY OK`. Baseline/head failure sets are empty; the exact skip list is unchanged. Final source hashes and log digests are in `validation.json`. Initial and intermediate runs are retained separately and are not counted as release evidence.

The HTTP-disabled browser fixture renders the real shared board and adapter with 150 synthetic signals and 600 option legs, uses a fixed market clock, and stubs row-action hooks. Its explicit premium targets are fixture data, not changes to strategy rules. No broker call or production trading setting is changed. Before/after builds use the baseline and patched marker stores/board respectively. See `browser-summary.json`.

| Interaction | Before notifications | After notifications |
|---|---:|---:|
| Clock refresh, unchanged entries | 300 | 0 |
| Change one winning leg | 300 | 1 |
| Open fixture Tools with clock refresh | 300 | 0 |
| Unmount board | 150 | 1 |

The best-R:R mark visibly moved from OPT1A to OPT1B; a separate row retained its mark. Fixture Tools opened in both builds. Single second-frame timings are retained for transparency, under concurrent test load; they are not a statistical latency benchmark or full-session acceptance. The fixture omits global app CSS; native per-row scrollbars in screenshots do not establish a production styling defect.

Private logs, red/green tests, harness sources/builds, source hashes, screenshots and browser snapshots: `.runtime/qa/2026-10-24/board-marker-batching/` in the primary checkout. Public tracked documentation contains synthetic results only.

## Scope and outstanding acceptance

This fixes confirmed refresh/ownership defects; it does not establish that every prior large-table automation timeout has the same cause. Actual production navigation is checked separately after deployment. Strategy mathematics, entries/exits, SL/TSL/targets, AUTO, allocation, lot limits and live execution are preserved. EMA/Gamma remain original only; PR #258's default/mode explanations remain in place.

All outstanding acceptance in the combined implementation ledger remains open: full same-release market session and recovery timing, Gamma two-day watch/restart, Snapback restored real rows, actual notification receipt, authoritative research/holdout/lineage, public-tunnel stability and hosted CI. A closed-market fixture cannot certify go-live readiness.

[Concrete remaining acceptance criteria](REMAINING_ACCEPTANCE.md) preserve every live/external obligation and threshold from the supplied specification.

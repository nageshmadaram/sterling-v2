# Health and market-observation truth — 26 October 2026

Base release: c2df68ebbb255a373b2f013c7d458a60862057df. This continues the operator's supplied items 7–21 and wider go-live review. Runtime controls, strategy maths, order admission and broker request budgets are unchanged.

## Fixes

The public shallow health endpoint previously reported zero positions/alerts, a running checker and broker reachability without observing those facts. It also read account storage on the event loop. It now explicitly reports `scope: liveness`, nullable unqueried counts with `status: not_checked`, `exchange_reachable: null` and `background_checker: not_checked`. It does not read an account or broker inventory. The authoritative execution-control field is retained. Authenticated deep health and broker inventory remain separate facts; shallow health cannot certify a flat account.

**API compatibility:** count keys and summary objects remain present, but count values are now nullable; checker status is `not_checked` rather than an invented running observation, and broker reachability stays unknown. External health consumers must handle unknown values and must not convert them to zero or treat liveness as broker/protection readiness. Repository consumers were checked; the old shallow-checker regression now expects the truthful scope. No broker data is exposed on this public route.

Signal-engine tabs derived their “scanned” count from loaded display-array length. They now identify enabled/disabled state, active display rows and loaded records. Active rows can be view-only and do not promise execution eligibility. Actual coverage stays with the engines' scan/job diagnostics; no count of processed instruments is fabricated. Disabled engines retain history counts. A stale comment describing the removed classic default was removed; shared rendering remains the only renderer.

PCR grids already assign a current print to a forming quarter-hour bucket. They now retain the actual source-print clock as metadata and show `Forming until 12:15 · observed 12:05` in tile, heat and flow tape. Completed rows distinguish slot time from observation time; unavailable source clocks are explicit. Label wrapping avoids truncating the disclosure. Bucket membership, PCR values, deltas, flow decisions and strategy rules are unchanged. Existing date/feed failure disclosures remain.

## Verification

The missing-account test first returned 500 on the original shallow endpoint; two execution-control cases passed. A separate checker test failed while the old hardcoded running claim remained. The fixed health/deep/session group passes nine cases. Three new board-count cases fail before the label fix and pass afterward. Five new PCR timing/integration cases fail before disclosure metadata; the final focused frontend group passes 32 cases, including explicit tile/heat/tape checks, exact boundary, closed market and stale source.

The initial direct frontend suite omitted the generated media prerequisite and failed two media checks (2,408 passes). The full corrected invocation uses npm test's pretest media build and passes 2,410 cases in 260 files. A failed relative-path log copy meant the initial raw frontend log was overwritten during rerun; its observed failure summary is retained privately, and no original raw-log hash or passing result is claimed. The complete corrected final log is retained. Full make verify passed: 11,861 backend cases / 30 unchanged skips, TypeScript and VERIFY OK. Exact skip reasons were independently rechecked (370 passed / 30 skipped) and match the baseline. Final source/log hashes and empty baseline/head failure sets are recorded in validation.json.

A clearly labelled local UI fixture uses simulated PCR data and a fixed 12:05 clock, serves no trading backend and rejects non-GET API requests. Browser accessibility inspection confirms all three surfaces show the forming/observed distinction and tabs describe loaded records. A desktop screenshot confirms tile text wraps visibly. This is isolated UI verification, not a live market session, a phone-viewport check or a real signal/fill.

## Storage readiness

The primary host fell below its unchanged 2 GiB readiness floor (about 1.5 GB free). Thirty older completed QA snapshot copies from 23–24 October were AES-GCM encrypted on the existing backup volume, authenticated/decrypted, gzip/digest checked and restored into temporary SQLite databases for integrity checks before local-copy removal. About 926 MB was recovered. Their local offload pointers and private recovery manifest/key are preserved; current production databases, production logs, recent rollback copies, original retired-worktree backups and trading settings were not changed. These archives are recoverable, not discarded. No plaintext sensitive backup was written to the backup volume.

## Live acceptance is separate

26 October market-close evidence still fails continuous same-release coverage: a 42-minute probe gap and mixed/unknown earlier release identities. Zero recorded stalls is not proof during that gap. Current-worker Gamma recovery was 238.73 seconds, 159 watches/no issue: the 180-second target remains failed. Earlier normal 13:15 scan completed in 70.57 seconds, independently below its ten-minute threshold. Simulation and two consecutive actual days remain unaccepted.

Snapback now has one genuinely saved SHREECEM row. Populated read-only restoration, fresh-scan replacement and AUTO retention remain unaccepted until observed. No production AUTO toggle, manufactured row, real order/GTT mutation or notification send is part of these fixes. Selected-mode board continuity during incremental scans remains open; safely retaining prior rows requires a canonical server-side stale-entry block, not merely redisplaying stale plans. Original Gamma Swing protection, EMA practice-only scope, research lineage/promotion, hosted billing and temporary-tunnel stability remain explicit limitations.

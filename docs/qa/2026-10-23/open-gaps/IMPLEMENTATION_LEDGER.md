# Combined implementation and acceptance ledger

Baseline inspected: `14c1875028c48bb27b8e81e1a953380ac172b919`.

This continues both the prior trader QA work and every item in the operator-supplied implementation spec. Repository paths and existing fixes were rechecked; the spec's `bd783b1f` line numbers are historical. Earlier partial/failed test logs are not release passes. The user explicitly requested that enabled trading remain enabled; document suggestions to turn AUTO off do not override that instruction. No broker order/GTT mutations are part of QA.

| Item | Scope | Current acceptance boundary | State |
|---|---|---|---|
| 9 | Market-load stall measurement | Implemented a read-only IST session audit; today has mixed pre-fix releases and fails the market threshold. Future full sessions remain required. | Implemented; acceptance pending |
| 12 | Remaining event-loop blockers | Async warehouse/risk/EOD/candle/indicator work offloaded; snapshot probes receive detached scan state. Focused snapshot/session/intraday group: 80 passed. Full-session measurement pending. | Implemented; acceptance pending |
| 10 | Simulation Gamma timeout | Gamma-specific 1,500-second deadline plus fresh owner-market-data reuse with a separate simulated engine. Exact-config/freshness fallback retained. Full-day timeout/watch acceptance pending. | Implemented; acceptance pending |
| 14 | Evidence config drift / log noise | HALTED remains authoritative; repeat logs throttled by IST day/status/reason. Frozen experiment and runtime settings preserved. | Implemented; automated verification passed |
| 11 | Gamma scan cost | Account-scoped persistent daily/5m cache with incremental tail refresh, integrity validation and failure propagation; completed selection slots cached. Zero live-OI strikes filtered before history. Actual recovery/scan duration acceptance pending. | Implemented; acceptance pending |
| 7 | Gamma live recovery acceptance | Existing untraded-strike recovery fix retained and regression checked. Two consecutive observed trading days and midday restart remain pending. | Implemented; acceptance pending |
| 8 | Snapback restored board acceptance | Existing persisted read-only restore and switch-retention fixes retained. Current saved snapshot has zero rows; real-signal restart acceptance remains pending. | Implemented; acceptance pending |
| 16 | Partial-suite isolation | Navigator temporary-DB binding leaks repaired; replay diagnostic initialized before first start; subprocess import path made explicit. Gamma 460 passed; SuperTrend 191 passed/1 existing skip; Snapback 3,011 passed/1 existing skip. Full baseline and HEAD failure sets are empty. | Implemented; see validation and pending acceptance |
| 17 | Lot cap / daily halt | Existing lot settings preserved (option B). Readiness estimates the median current active-contract stop risk and warns when the lot ceiling exceeds the halt; missing evidence stays unknown. | Implemented; automated verification passed |
| 13 | One SuperTrend renderer | Shared board is the sole SuperTrend renderer. Classic-only dead code/setter removed; persisted preference retired at store version 14; safety tags share the adapter helper. | Implemented; automated verification passed |
| 15 | Presentation tunnel status | All/some/no verified providers map to ready/degraded/reconnecting. Publication selects a verified working endpoint, including secondary-provider fallback; 87 stack tests passed. | Implemented; automated verification passed |
| 19 | Practice-only strategies | Persistent practice-only guide tag; EMA Buy column and generic Adaptive Edge manual action hidden. Legitimate practice detail remains accessible. | Implemented; automated verification passed |
| 21 | Retire old worktrees | Three clean, merged, idle legacy worktrees removed after four encrypted online DB backups passed authenticated restore/integrity checks. Current verification worktrees remain until completion. | Implemented; see validation and pending acceptance |
| 18 | Strategy evidence | Data-only snapshot: 128,437 daily bars, 204 symbols, 2024-04-04 onward. Configured hedged Snapback one-lot model: 198 OOS trades, positive model net and reported p=0.0033; stricter authoritative/exposure/lineage requirements remain unproven; no promotion records or trading controls written. Other mode evidence is separately unproven. | Implemented; acceptance pending |
| 20 | Snapback quiet-period check | Stored-bar 30-session check returned 30 historical modelled candidates; not a 30-session no-signal result. No threshold/filter changes. Weekly Friday read-only follow-up created; daily market acceptance follow-up also created, without restart/order/control permissions. | Implemented; see validation and pending acceptance |

Prior work remains tracked in `../PASTED_REPORT_RECONCILIATION.md`, `../OPS_THEME_FOLLOWUP.md`, `../SYNC_AND_FOLLOWUP.md` and `../../2026-10-22/FIX_IMPLEMENTATION.md`. This ledger will reconcile their stale states against final source/runtime evidence, retaining any unresolved original finding.

Final automated results and source paths: [VALIDATION.md](VALIDATION.md). Deployment identity is recorded separately after integration.

Subsequent remaining-gap work: [CONTINUATION.md](../remaining-gaps/CONTINUATION.md). This adds global session visibility, correct notification workspace routing, truthful research/help copy, expanded-ticket current-price safety/distance, exact 38-pair EMA recovery evidence, logged-out scheduler verification and continuous read-only session coverage. Live-day and authoritative research requirements remain explicitly pending.

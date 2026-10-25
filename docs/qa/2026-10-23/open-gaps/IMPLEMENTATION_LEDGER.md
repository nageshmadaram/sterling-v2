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

24 October original-strategy follow-up: the operator supplied the EMA source post and requested no added EMA/Gamma horizons. [Original-mode continuation](../../2026-10-24/strategy-mode-truth/CONTINUATION.md) tracks the Daily/Weekly-only EMA operational policy, preserved legacy exits/history, unchanged Gamma rules and corrected optional-filter signal explanations. The [strategy mode guide](../../../strategy/STRATEGY_MODE_GUIDE.md) explains defaults, saved settings and each strategy's entries/exits. Existing live-session, notification, recovery and authoritative research acceptance requirements remain open.

24 October readiness follow-up: [Public readiness diagnostics](../../2026-10-24/readiness-diagnostics/CONTINUATION.md) reproduces missing timeout/route reasons and the stale supervisor-recovery warning after API recovery. It adds bounded, credential-safe diagnostics and fresh-health status filtering while retaining the current temporary tunnel and trading controls. It does not certify the outstanding live-market or external acceptance items.

24 October historical UI follow-up: [Gamma history access and request status](../../2026-10-24/gamma-history-status/CONTINUATION.md) restores the unreachable day/coverage controls, distinguishes background backfill from selected-day loading and adds a dated, explicit retry. Historical/replay entry guards and Gamma original rules remain unchanged. Live-session and external acceptance requirements remain open.

### 24 October — default/mode clarity continuation

See [default tags and effective mode configuration](../../2026-10-24/mode-default-clarity/CONTINUATION.md). Default labels and mode configuration explanations are implemented; release validation is recorded with that continuation. Real-session acceptance remains separate.

### 24 October — marker refresh and ownership continuation

[Atomic marker updates and independent board/mode ownership](../../2026-10-24/board-marker-batching/CONTINUATION.md) fixes confirmed refresh churn, temporary marker disappearance, simultaneous mode/timeframe collisions and cross-board unmount clearing. It also removes the remaining hardcoded hourly empty notice. Synthetic before/after browser counts and final automated validation accompany the continuation; full-session and external acceptance remain open.

### 25 October — manual ticket handoff and user-TV synchronization

[Manual entry ticket provenance, new-plan reset and delayed-response isolation](../../2026-10-25/manual-ticket-plan/CONTINUATION.md) fixes confirmed client handoff defects while retaining server admission and strategy-owned execution. It also preserves the operator's standalone TV update in the hosted canonical source and restores compile/embed/export idempotence, with the isolated subtitle preference bridge. Final automated results accompany the continuation; all real-session/external acceptance stays open.

### 25 October — broker read availability

[Unknown inventory, retained snapshot warnings and safe outputs](../../2026-10-25/broker-read-visibility/CONTINUATION.md) fixes false empty orders/portfolio states, a missing-data Positions render loop and analysis/CSV output from failed reads. Existing exits and strategy policy remain unchanged. A successful login is distinguished from unreadable positions/orders broker endpoints; runtime promotion and external acceptance remain separately recorded.

### 25 October — notification pane destination and device recovery

[Notification destination continuation](../../2026-10-25/notification-destination/CONTINUATION.md) records user-confirmed receipt/navigation after replacing a retired-origin device registration. It also fixes a separate reproduced mismatch between the shell's notification destination and KiteTab's saved/default pane. Final validation and runtime promotion are recorded separately; other real-session and external acceptance gates remain open.

### 25 October — session evidence after restart

[Damaged probe input and durable writes](../../2026-10-25/probe-log-recovery/CONTINUATION.md) fixes the session-audit CLI crash on a real damaged JSONL record. Malformed evidence remains explicitly unverified; raw input is archived unchanged. Standalone probe writes are synced, and an archived/new-segment boundary is recorded separately rather than repairing history. Runtime and full regression results accompany the continuation; live-session acceptance remains pending.

### 25 October — Gamma scheduler calendar admission

[Automatic scan calendar guard](../../2026-10-25/gamma-session-calendar/CONTINUATION.md) reproduces scheduled history/selection on closed exchange days. Verified-calendar admission now skips automatic scan work on closed/unknown days while retaining reconciliation and owned-position maintenance. Existing historical evidence is preserved; real market-day acceptance remains pending.

The same continuation includes the operator's actual phone screenshot: Positions visible beneath a stale PCR heading. Shared saved-page initialization and early visible-pane title subscription fix the reproduced shell mismatch. Signals/default-label and final-device visual acceptance remain pending; the screenshot does not establish them.

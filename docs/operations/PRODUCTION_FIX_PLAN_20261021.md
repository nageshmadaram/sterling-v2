# Sterling production fix plan — 2026-10-21

Scope: SuperTrend, Snapback and Gamma Unwind only. Signals, entry, SL, TSL,
exit, alerts, signal tables, UI copy, paper-mode removal, speed, security,
Telegram, sync. This document lists what to fix and in which order. **Nothing in
it has been fixed yet.** Every finding below was measured on this host today on
checkout `fa0221ab` (branch `dev`), unless it is marked as carried from an
earlier audit.

---

## 0. Read this first: the money question

The software can be made release-ready by tomorrow. Whether it will make money
is a separate question, and the evidence in this repository says it has **not
been proven** for any of the three strategies:

| Strategy | Best evidence on record | What it means for real money |
|---|---|---|
| SuperTrend | Long timing beats random exposure (p=0.0006). The short book nets zero, so `allow_short=False`. | Strongest of the three. It is still a backtest, not live results. |
| Snapback | Directional put lost (−1.08% OOS, p=0.37). Market-neutral version passed (+3.04% OOS, p=0.016), but option prices are **modelled**. Kite sells no expired-option history. | Run only hedged (`hedge_mode="index_futures"`, the current default). The status screen says it needs 300+ real trades across 60+ sessions before it counts as proven. |
| Gamma Unwind | Calibrated on 193k real OI bars. Result is **negative**: the entry trigger has no edge; only the level filter does. | Do not size it as an earning strategy. Keep `auto_execute=False` (current default) or run it at minimum size as a recorder. |

Recommendation for launch: start with the smallest real size the broker allows
(1 lot), a hard daily loss cap, and SuperTrend first. Increase size only after
real fills show profit. A bad week at full size can lose money the family needs.
This is engineering advice, not financial advice.

---

## 1. Measured state today

| Check | Result |
|---|---|
| Frontend `tsc --noEmit` | PASS |
| Frontend Vitest | PASS — 229 files, 2178 tests |
| Backend SuperTrend group (engine + freeze + parity) | PASS — 932 |
| Backend Snapback lifecycle group | PASS — 326, 1 skipped |
| Backend Gamma Unwind lifecycle group | PASS — 135 |
| Backend Telegram outbox group | PASS — 51 |
| Backend `tests/e2e_live` | PASS — 42 (offline doubles; 2 min 48 s) |
| Full backend suite | not finished: ~3% after 10 min, stopped. See P1-PERF-3. |
| `sterling-status` | Backend RUNNING, Ready = **RECOVERY_REQUIRED** |
| `sterlingctl doctor` | **FAILED**: `allocation_capital`, `disk`, `risk_limits`, `account_binding`; COULD NOT CHECK: `network_path` |
| Env | `ENVIRONMENT=production`, `STERLING_LIVE_EXECUTION=true` |
| Safe mode | `NORMAL` (released 2026-10-19 with reason text "test (resumed …)") |
| Root disk | **99% full, 1.8 GB free** |
| `sterling-web.service` (systemd) | **failed** — 13 restarts, then "Start request repeated too quickly" (2026-10-20 22:14). Gateway currently up by another route. |
| Kite token | expires 2026-10-22 06:00 IST — daily re-login required |
| Accounts table | 1 live (`KITE-…FE4E`, user `default`), 1 simulation (`SIM-…`, user `sim:default`, still `is_active=1`) |

---

## 2. P0 — blocks any real-money entry

Fix all of these before the first live order. Most are operator actions, not code.

| ID | Finding | Fix | Who |
|---|---|---|---|
| P0-1 | **Worsened, then partly fixed 2026-10-21:** hit 0 bytes free at ~16:45 IST; tool caches (codex-runtimes, ms-playwright, uv, pip) removed → 2.7 GB free. Still below the 10 GB target. Root disk 99% full (1.8 GB free). Doctor minimum is 2 GiB. SQLite with WAL on a full disk can fail a write in the middle of an entry or exit. | Free ≥10 GB. Candidates: `data/sterling_paper.db` (809 MB, last written Oct 9 — the live DB is `backend/sterling_paper.db`; confirm before moving), old `.runtime/stack/releases/*`, `node_modules/.vite`, pip/npm caches. Move, do not delete, databases and backups — copy to the lake drive first. | Operator, with Claude listing candidates |
| P0-2 | `STERLING_ALLOCATION_CAPITAL_INR` not set. | Set it in `backend/.env` to the real capital you will risk. | Operator |
| P0-3 | `STERLING_RISK_LIMITS` not set — the four-level risk hierarchy is **not enforced**. Only capacity arithmetic applies. | Set global + per-lane caps (max lots, max premium at risk per trade, daily loss). Doctor already checks the format. | Operator, Claude drafts values |
| P0-4 | `account_binding`: "no usable broker account binding". | Run the binding flow after today's Kite login; confirm `sterlingctl bindings` shows ACTIVE. | Operator |
| P0-5 | `network_path` COULD NOT CHECK. | Refresh binding (P0-4) or record broker reachability. | Operator |
| P0-6 | **Done 2026-10-21 16:53 IST:** safe mode ON (reason: disk full + failing checks). Safe mode was released with reason "test". Live execution flag is already `true`. | Engage safe mode now. Release it only after P0-1…P0-5 pass, with a real reason. | Operator |
| P0-7 | Static egress: doctor says the IP matches, but the matched IP **changed** from an IPv6 address. Zerodha requires a whitelisted static IP for order APIs. | Confirm in the Kite developer console that the current IPv4 is the registered one. A home/mobile IP that changes will reject orders mid-day. | Operator |
| P0-8 | **Cause found 2026-10-21:** EADDRINUSE on 5180 — a manually started `sterling_stack.py run --all` already owned the gateway, so the systemd unit crash-looped. Launcher now logs the reason to `manager.log`. Operator: run the stack under ONE owner (systemd *or* manual). Original: `sterling-web.service` failed under systemd. If the gateway dies, the UI and the Telegram desk go dark while trades stay open. | Read `.runtime/stack/*.log` for the `RuntimeError`; fix the launch; `systemctl --user reset-failed` and restart. | Claude |
| P0-9 | Cloudflare quick-tunnel URL changes on every restart (`trycloudflare.com`). The Kite redirect URL and the Telegram desk link break each time. | Use a named Cloudflare tunnel with a fixed hostname, or update the Kite console redirect every restart. | Operator + Claude |
| P0-10 | Running source is not the frozen experiment config: `source_identity_mismatch`, `config_hash_mismatch`. | After fixes, commit, restart the backend once, confirm Build line = HEAD. | Claude |

---

## 3. P1 — strategy lifecycle

### 3.1 SuperTrend

| ID | Finding | Fix |
|---|---|---|
| ST-1 | Two renderers. Default is the shared `SignalBoard`, but the old `classic` table still lives inside `SterlingKiteEnginePane.tsx` (2860 lines, branch at line 2552). Two code paths to keep in sync. | Remove the classic renderer and its setting after confirming day-grouping parity in the shared board. |
| ST-2 | **Checked 2026-10-21, no bug:** callers pass `gtt_id` only for the stop they just placed/moved, so local stop and broker GTT agree. **New ST-5 below.** Original: `positions.update_stop` sets `p.gtt_id` even when it ignores a looser stop (`positions.py:437-446`). If a caller ever modifies the broker GTT to a looser level and passes the new GTT id, local state shows a tighter stop than the broker holds. | Only accept `gtt_id` when the stop actually moved, or assert the caller never loosens. Add a test. |
| ST-3 | Lane origination: doctor lists 5 SuperTrend lanes allowed (`ultra_scalping` … `swing`). Memory notes sub-hour timeframes lose to fees. | Disable `ultra_scalping` and `scalping` lanes for real money until they have live evidence. |
| ST-5 | **Open — operator decision.** A scale-in (second BUY of a held contract) re-registers the position with the NEW plan stop and moves the broker GTT to it (`protection.py:345-403`). If the first lot had trailed higher, protection loosens. | Choose: keep (stop re-based on the combined entry), or keep max(trailed, new) for longs. Do not change without a test. |
| ST-4 | Telegram alert names: `SL ARMED`, `TSL UPDATED`, `POSITION CLOSED`. These are the reference set; other engines should match them (see TG-1). | None. Reference. |

### 3.2 Snapback

| ID | Finding | Fix |
|---|---|---|
| SB-1 | Doctor: Snapback preflight FAILED (`worktree_clean`, `config_identity`, `evidence_meta`, `dataset_start`, `allocation_capital`, `disk`). Several are waived but still printed as FAILED, which confuses the operator. | Fix P0-1/P0-2. Make the preflight line print only non-waived failures. |
| SB-2 | ~~No `SL ARMED` alert~~ **Withdrawn 2026-10-21.** The live path sends `SL ARMED` (`snapback_live_wiring.py:477`) through a local `alert(...)` helper the first search missed. | None. |
| SB-3 | ~~No `PROTECTION FAILED` alert~~ **Withdrawn 2026-10-21.** Sent at CRITICAL with kill switch (`snapback_live_wiring.py:695`). | None. |
| SB-4 | Hedge is mandatory for the edge. Confirm live entry refuses when the futures hedge leg fails, instead of leaving a naked option. `HEDGE_WAIVED` exists as an event. | Audit every `HEDGE_WAIVED` path; for real money, waiving must close the option leg. |
| SB-5 | Status economic gate says "BLOCKED — strategy is unproven". This is correct and must stay visible. Do not hide it to make the UI look ready. | Keep. Shorten the text (see UI-3). |

### 3.3 Gamma Unwind

| ID | Finding | Fix |
|---|---|---|
| GM-1 | **Fixed 2026-10-21 (report only).** Gamma IS gated, by `release_scope.RELEASE_ORIGINATORS`; the doctor focus line now names it. Original: Gamma is **not** in the focus policy. `FocusedStrategy = Literal["snapback", "supertrend"]` (`app/core/focus.py:17`). Doctor shows only those two as originators. Gamma entries are governed only by its own `auto_execute` flag. | Decide: either add `gamma_unwind` to the focus policy so one switch governs all three, or document that Gamma is manual-only. One governance model, not two. |
| GM-2 | **Fixed 2026-10-21.** Trailing alert was named `TSL SYNCED`, not `TSL UPDATED`. Also emits `T1 / BREAKEVEN`, `STOP TRIGGERED, SELL NOT FILLING`. | Rename to the shared set (TG-1). Keep the extra events, use the same style. |
| GM-3 | `entry_trigger = "live_breakout"` default. Research says the trigger has no edge. | Keep `auto_execute=False`. Show "Manual" clearly on the board. |
| GM-4 | **Partly fixed.** Message is now "Broker not connected." The `is_paper` checks stay: they fail closed when the broker mode is unknown. | Replace with live/blocked states only (see PM-2). |
| GM-5 | Gamma board has its own `<table>` for history cards (`GammaMoveBoard.tsx:202`) beside the shared `SignalBoard`. | Move history to the shared table spec or accept as a separate view; match font sizes (`ROW_METRICS`). |

---

## 4. Telegram

| ID | Finding | Fix |
|---|---|---|
| TG-1 | Lifecycle event names differ by engine. | One table for all three: `SIGNAL`, `ENTRY FILLED`, `SL ARMED`, `TSL UPDATED`, `PARTIAL EXIT`, `POSITION CLOSED`, `PROTECTION FAILED`, `ORDER OUTCOME UNKNOWN`. Gamma `TSL SYNCED` → `TSL UPDATED` done. SB-2/SB-3 were already present. |
| TG-2 | Delivery is proven in tests (51 pass) but not on a phone. | Send one test event per type; confirm receipt on the phone. |
| TG-3 | Mini App URL is on the rotating tunnel (P0-9). | Fix with P0-9. |
| TG-4 | **Checked, no bug:** live manual BUY is capped at `cfg.max_lots × lot_size` (`service.py:316`). Original: Mini App `place_order` accepts any symbol/side/qty ≤100000 when `signal` is not true (`tma.py:303-326`). It goes through `place_manual_order`, which applies the live-safety gate. | Confirm the safety gate's lot cap applies here; lower the 100000 bound to the configured max lots. |

---

## 5. Paper mode removal

Backend already treats every new account as live (`accounts.py:13`, `:518`). What is left:

| ID | Item | Fix |
|---|---|---|
| PM-1 | `SIM-…` simulation account active for `sim:default`. | Deactivate. Do not delete the row (audit history). |
| PM-2 | Frontend `is_paper` branches: `GammaMoveBoard.tsx`, `types/kite.ts`, `types/kiteDiagnostics.ts`, `ConnectPane.tsx:521` label cleanup. | Remove UI branches; keep the type field optional so old payloads parse. |
| PM-3 | **Fixed 2026-10-21.** Removed from tabs, re-scan, switches, sim list, Operations card; its queries no longer poll. Original: EMA Trend is labelled "paper" in `useAlgoToggles.ts:77-79`, `useEngineToggles.ts:82-84`, `useSim.ts:25`, `TradingModePanel.tsx:100`, `LiveOperationsPanel.tsx:122-126`. EMA Trend is outside the three strategies. | Hide EMA Trend from navigation and toggles. Do not delete its code in this release. |
| PM-4 | Backend `KiteClient` paper simulation (`client.py`, 57 hits) and Snapback observation warehouse (92 hits). These back the test suite. | **Keep.** Removing them breaks ~1400 tests and gains nothing for users. Ensure `ENVIRONMENT=production` refuses an `is_paper` account. |
| PM-5 | Live database file is named `sterling_paper.db`. | **Do not rename** the day before release. Every path, backup manifest and restore proof references it. Rename later. |

---

## 6. UI consistency and copy

| ID | Finding | Fix |
|---|---|---|
| UI-1 | **Checked, consistent:** all three adapters map ≤0/NaN prices to null (rendered —). Shared row spec exists (`signalRowSpec.ts`): columns Exc., Leg (Δ), Entry (Δpts), SL, TSL, Exit, Target, Chg., Chg. %, LTP, Time, Trade, Chart. All three boards import `SignalBoard`. | Verify each adapter fills SL, TSL, Exit, Target the same way; blank cell = "—", never `₹0.00`. |
| UI-2 | **Checked, already unified:** one `BoardStatus` set (Armed/Running/Weakening/Ended/Watching/Error) in `boardTypes.ts`. Status words differ: Snapback/SuperTrend use `entry_ok`; Gamma uses tiers (`4/4 ENTRY`, watchlist). | Map all to one chip set: Watch · Ready · Open · Exited · Blocked. |
| UI-3 | Long copy on boards and status (e.g. "Watch contracts are selected at the reference 09:20/13:15 builds. E1 is the breakout event; only 4/4 ENTRY is executable."). | Cut to one short line; move detail into a tooltip. |
| UI-4 | **Fixed with PM-3.** Navigation still showed strategies outside scope (EMA Trend, others in `TradingModePanel`). | Show only SuperTrend, Snapback, Gamma Unwind. |

---

## 7. Speed, security, sync

| ID | Finding | Fix |
|---|---|---|
| PERF-1 | **Withdrawn:** Python `sqlite3.connect` waits 5 s by default; the lock came from the `sqlite3` CLI (0 s wait). Read-only `sqlite3` on the live DB returned `database is locked (5)` once without a busy timeout. | Check for long write transactions in scanners; confirm all app connections set `busy_timeout`. |
| PERF-2 | **Already fixed:** `useKitePositions` defaults to 5 s. Positions hook polled every 1 s (carried from issue register UI-01). | Confirm current interval; use WebSocket push, poll ≥5 s as fallback. |
| PERF-3 | Full backend suite: ~3% in 10 minutes on this host. A full regression run before each release is not practical. | Find slow tests (`--durations=30`); add `pytest-xdist`; mark slow tests. |
| SEC-1 | `.env` holds broker and Telegram secrets on a 99%-full single disk. | Back up encrypted to the lake drive (P0-1). |
| SEC-2 | Public quick tunnels expose the gateway to the internet. | Confirm every trading route requires auth; Mini App requires Telegram `initData` signature (`telegram_webapp_auth.py`). |
| SYNC-1 | `main` and `dev` must point to one SHA for release. | Fast-forward only, per `feedback_branch_unification_sync`. |

---

## 8. Fix order for tomorrow

1. **Operator, now:** engage safe mode (P0-6). Free disk (P0-1).
2. **Claude:** P0-8 web service, P0-10 identity, TG-1/SB-2/SB-3 alerts, GM-1 governance, PM-1…PM-3, UI-3/UI-4, ST-2.
3. **Claude:** rerun the four lifecycle groups + frontend suite; must stay green.
4. **Operator:** set P0-2/P0-3 values, Kite login, binding (P0-4), confirm egress in Kite console (P0-7).
5. **Operator + Claude:** `sterlingctl doctor` must show zero FAILED. Phone receipt of every Telegram event (TG-2).
6. **Operator:** release safe mode. First live trade: SuperTrend, 1 lot, during market hours, watched. Confirm entry fill → SL at broker (GTT visible in Kite) → TSL moves → exit and P&L match Kite.
7. Snapback and Gamma follow only after step 6 works end to end.

## 9. Do not change

- Exit paths must take `_exiting` claim (`monitor.py:121`). Never add an exit that skips it.
- Stops only ratchet in the protective direction.
- Safe mode blocks new entries, never exits.
- Do not delete databases, journals, backups or encryption keys while freeing disk.
- See `docs/operations/WHAT_NOT_TO_CHANGE.md`.

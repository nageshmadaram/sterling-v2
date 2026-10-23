# Open gaps — implementation spec (items 7–21)

Baseline: `main` = `dev` = `bd783b1f` (2026-10-23).
Owner: operator. Each item lists the problem, where the code is, the change to make,
how to test it, and what "done" means. Line numbers are for `bd783b1f` and will drift.

Conventions used throughout:

- Run backend tests with `cd backend && env -u STERLING_DB_PATH PYTHONWARNINGS=ignore .venv/bin/python -m pytest -q -p no:cacheprovider <path>`.
  Never run the suite with `STERLING_DB_PATH` pointing at `backend/sterling_paper.db`.
- Run frontend tests with `cd frontend && npx vitest run <path>` and `npx tsc --noEmit -p .`.
- Blocking work inside `async def` goes through `await run_blocking(fn, *args)` from
  `app.core.async_work`. Never call SQLite, a full-table read or numpy indicator math
  directly on the event loop.
- Commit by file name (never `git add -A`), then sync: `git fetch -q . dev:main && git push -q origin dev main`,
  then `systemctl --user restart sterling-backend`.

Priority order: 9 → 12 → 10 → 14 → 11 → 7/8 (verification) → 16 → 17 → 13 → 15 → 19 → 21 → 18 → 20.

---

## 7. Verify the Gamma Unwind watchlist-recovery fix (live)

**Problem.** From 2026-10-04 to 10-23, every mid-day restart left Gamma Unwind watching
nothing: one watchlist whose strikes had not traded with OI by the build time was counted as
a recovery failure, and `clear_runtime(uid)` dropped every watch (15,404 failed cycles).
Fixed in `bd783b1f` but not yet observed on a live session.

**Code.** `backend/app/services/gamma_move_historical.py` (`UNTRADED_NOTE`, `_watches_at`),
`backend/app/services/gamma_move_scanner.py` (`recover_scheduled_watchlist`, `hard_failures`).

**Verification steps (next trading day).**
1. Log in to Kite before 09:15.
2. After 09:20, open KITE → Gamma Unwind. The scan bar must show `watched > 0`.
3. Run:
   ```bash
   grep "$(date +%F)" backend/logs/backend.log | grep "gamma_unwind cycle failed for default: historical"
   ```
   Expected: no output.
4. Restart the backend once between 10:00 and 13:00 and repeat steps 2–3. The recovery path
   must rebuild the watches (stats `recovery_source = scheduled_history`, `issue = ""`).

**If it still fails.** Read the first entry of `scan.coverage` in
`GET /api/v1/config/gamma-move/snapshot`. If it is a fetch error (timeout, 429, network),
that is a genuine hard failure; decide whether one failed contract should drop the whole
runtime. Recommended follow-up: drop only the affected `(underlying, list_name)` pair instead
of calling `clear_runtime(uid)`.

**Done when.** Two consecutive trading days with no "historical watchlist recovery incomplete"
lines and a non-zero watched count after a mid-day restart.

---

## 8. Verify Snapback board restore after restart (live)

**Problem.** The Snapback board was empty for ~10 minutes after every restart. Fixed in
`ee3cc2b9` and `8d8d29e2`: the last scan's rows are saved and restored read-only; on/off and
AUTO switches no longer clear rows. Not yet seen live because no Snapback signal has fired
since 2026-10-19.

**Code.** `backend/app/services/snapback.py` (`_restored_rows`, `_save_last_rows`,
`_SWITCH_ONLY_KEYS`, `_aged_rows` restored branch), `snapback_readiness.py`
(`restored_from_last_scan` reason).

**Verification steps.**
1. Wait for a scan that produces rows (`GET /api/v1/config/snapback/snapshot` → `rows.length > 0`).
2. Confirm the saved copy exists: `system_config` key `snapback.last_rows.v1:default` longer than 100 bytes.
3. Restart the backend and open Snapback within 30 seconds.
4. Expected: the rows appear immediately, state `watching`, Buy disabled, block reason
   "Shown from the last scan before a restart…". After the first fresh scan, the restored
   flag disappears.
5. Toggle AUTO on and off. Rows must stay.

**Done when.** Observed once on a live day with signals.

---

## 9. Measure event-loop stalls under market load

**Problem.** Stalls fell from 34 to 0 after today's fixes, but that was measured after the
close. A stall freezes order placement and exits for its whole duration.

**Measurement.** At 15:30 on the next trading day:
```bash
grep "$(date +%F)" backend/logs/backend.log | grep "Event loop stalled" \
  | grep -o "locations=.*" | awk -F' > ' '{print $(NF-2)" > "$(NF-1)" > "$NF}' \
  | sort | uniq -c | sort -rn
```
Each line names the frame that blocked the loop. Fix the top entries using the pattern in
item 12.

**Done when.** Fewer than 3 stalls per trading day, none longer than 5 s.

---

## 10. Simulated Gamma pass always times out

**Problem.** `simulation gamma pass for sim:default timed out after 300s; cut off` on every
cycle. A full Gamma scan takes 17+ minutes, so the virtual tenant never completes one, and
the simulation shows no Gamma activity.

**Code.**
- `backend/app/services/sim_scan_lane.py:21` — `PASS_TIMEOUT_S = 300.0`.
- `backend/app/services/gamma_move_runner.py:2466` —
  `sim_scan_lane.spawn("gamma", uid, lambda uid=uid: _scan_uid(uid, _now(), {}))`.

**Option A (quick).** Pass a Gamma-specific timeout at the call site:
```python
sim_scan_lane.spawn("gamma", uid, lambda uid=uid: _scan_uid(uid, _now(), {}),
                    timeout_s=SIM_GAMMA_TIMEOUT_S)  # e.g. 1500.0
```
Safe because `spawn` refuses to start a second pass while one is running and the pass runs
with `BACKGROUND_CALLS` set (it yields Kite rate budget to real trading).

**Option B (recommended).** The virtual tenant should not repeat the live scan's Kite reads.
Have `_scan_uid` for a sim uid reuse the live tenant's last watchlist build
(`gamma_move_scanner._runtime[<live uid>]` and the stage-A/B results) and only run its own
engine state. This removes ~400 Kite calls per cycle.

**Tests.**
- Unit: with a factory that sleeps longer than 300 s (patch `asyncio.sleep`), Gamma passes
  are not cut off when `timeout_s` is provided.
- Option B: a sim pass makes zero `get_historical` calls when a live build exists.

**Done when.** No "timed out" lines for `kind=gamma` in a full trading day, and the
simulation shows Gamma watches.

---

## 11. Gamma scans are slow (17–66 minutes)

**Problem.** Recovery after a restart took 1,052 s today; full scans have taken 66 minutes.
Watchlists are therefore stale or late.

**Code.**
- `backend/app/services/gamma_move_scanner.py:22-23` — `_HISTORICAL_RATE = 2.6`, `_QUOTE_RATE = 0.8`.
- `backend/app/services/gamma_move_historical.py:39` — `_HIST_RATE = 2.6`.
- Stage A (~213 underlyings), stage B (candidates), stage C (184 historical contract reads).

**Changes.**
1. **Day cache for underlying bars.** Key `(token, interval, day)`; store daily and 5-minute
   bars in memory (and optionally in `ohlcv_store`). A restart within the day re-reads only
   bars after the cached last bar.
2. **Pre-filter strikes with the live quote.** Before calling history for a structural
   candidate, read its current quote; if `oi == 0` and `volume == 0`, skip it — it cannot be
   selected (`_historical_contract_quote` requires `oi > 0`).
3. **Recover only missing slots.** `recover_scheduled_watchlist` loops every due build time;
   persist each slot's selected watches (`gamma_move_history.record_scan` already stores the
   watchlist) and reload them instead of rebuilding from history.
4. Do not raise the rates above Kite's 3 requests/second historical limit.

**Tests.**
- A recovery run with a cached day performs zero historical calls for underlyings.
- A strike with live `oi == 0` is never sent to `get_historical`.

**Done when.** Recovery after a restart completes in under 3 minutes; scheduled scans in
under 10.

---

## 12. Remaining event-loop blockers

**Problem.** Known synchronous work still on the loop.

**Targets.**

| Function | Location | Callers to change |
|---|---|---|
| `get_active_paper_positions` | `snapback_observation_warehouse.py` | `snapback.py:1491`, `snapback.py:1689`, `snapback_prospective_collector.py:864`, `api/v1/endpoints/lanes.py:292`, `snapback_runner.py:90`, `snapback_eod.py:156`, `snapback_eod.py:277` |
| `get_candles` | `ohlcv_store.py:116` | every caller inside an `async def` (find with `rg -n "get_candles\(" backend/app`) |
| Indicator math | `compute_atr`, `compute_ema`, `lfilter`, `cov` | the async scan loops that call them |

**Change pattern.**
```python
from app.core.async_work import run_blocking
positions = await run_blocking(warehouse.get_active_paper_positions)
```
- Only change callers that are inside `async def`. Synchronous callers that already run in a
  worker thread stay as they are.
- For tick-rate callers, use the 1-second cache pattern already in
  `snapback_stop_monitor.py` instead of a thread hop per tick.
- Keep `run_blocking` around pure functions only; do not mutate shared state from the worker.

**Tests.** Copy `backend/tests/services/test_snapback_scan_offload.py`: patch the target
function with one that asserts `threading.get_ident() != loop_thread`, run the async caller,
and assert it was called.

**Done when.** Item 9's measurement shows none of these frames.

---

## 13. SuperTrend classic board path

**Status.** `frontend/src/store/useKiteSettings.ts:227` already defaults
`boardRenderer: 'shared'`, and the shared board groups by day (`groupByDay` in
`SignalBoard.tsx`). New users get the shared board. Users with a persisted `classic`
preference still see the old layout.

**Change.**
1. Migrate persisted state: bump the store version and map `boardRenderer: 'classic'` to
   `'shared'` in the `migrate` function.
2. Confirm parity on the shared board: leg rows show safety tags (NO STOP, STOP ≥ ENTRY,
   EXPIRES SOON, VIEW ONLY), "Replaced" legs, closed-row reasons and 24-hour times — all
   exist in the classic path in `SterlingKiteEnginePane.tsx` (`legSafetyTags`, `legPlanOk`).
3. When parity tests pass, delete the classic branch in `SterlingKiteEnginePane.tsx`, the
   `setBoardRenderer` setter and its tests.

**Tests.** `SignalBoard.capabilities.test.tsx` and `SterlingKiteEnginePane.boardCapabilities.test.tsx`
cover both paths today; port the classic-only assertions to the shared path before deleting.

**Done when.** One SuperTrend renderer; all leg-row behaviours covered by shared-board tests.

---

## 14. Snapback evidence runner halted (`config_hash_mismatch`)

**Problem.** About 120 errors a day: `Snapback preflight FAILED (HALTED): ['config_hash_mismatch']`.
The prospective evidence runner does not start. Live Snapback is unaffected.

**Code.** `backend/app/services/snapback_startup.py:26` (`FROZEN_CONFIG_HASH = "6ecbeb53e9768a91"`)
and `:178` (comparison).

**Decision required (operator).**
- **Keep the experiment:** restore the Snapback config to the frozen one. The current hash
  is in the preflight `details.config_hash`; diff the live config against the manifest
  config and revert the changed fields.
- **End the experiment:** record the end in the evidence manifest, update `FROZEN_CONFIG_HASH`
  to the new config's hash in the same commit, and restart.

**Log noise fix (either way).** Log the HALTED result once per day per reason (keep the last
logged reason and date in module state), the same throttle used in
`gamma_move_runner._last_failure`.

**Done when.** The preflight is READY, or the halt logs once per day.

---

## 15. Stack status shows "reconnecting"

**Code.** `scripts/sterling_stack.py:1056-1065`. The phase is `ready` only when the primary
(Cloudflare) tunnel is verified; otherwise `reconnecting`, regardless of ngrok.

**Change.** Three states:
- `ready` — every configured provider verified;
- `degraded` — at least one verified (set `public_errors` for the others);
- `reconnecting` — none verified.

Update any consumer that string-matches `phase == 'ready'` (the `status` command output and the
Telegram menu publisher) so `degraded` still publishes the working URL.

**Done when.** With ngrok blocked and Cloudflare up, `status` shows `degraded` and the
Cloudflare URL under `urls`.

---

## 16. Tests that fail only in partial runs

**Problem.** Running a subset (`-k gamma`, `-k supertrend`) shows failures and errors in
`tests/unit/simulation/test_tenancy_mode.py`, `tests/unit/simulation/test_sim_halt.py`,
`tests/api/test_simulation.py`, `tests/unit/test_live_runtime.py`. The full suite passes.

**Cause.** A fixture relies on module-level state that some earlier test leaves behind.

**Steps.**
1. Reproduce: `pytest -q -k gamma` from `backend/`.
2. Run the failing test alone; if it passes, bisect with `pytest -p no:randomly <file>::<test>`
   plus the tests that ran before it.
3. Identify the module cache or setting the test depends on (tenant mode, sim identity,
   `_state` dicts, `_initialised_paths`-style caches).
4. Reset it in the test's own fixture, as `test_system_alerts.py` does with
   `outbox._initialised_paths.discard(path)`.

**Done when.** `pytest -q -k gamma`, `-k supertrend` and `-k snapback` all pass.

---

## 17. Lot cap versus daily-loss halt

**Facts (2026-10-23 signals).** One lot's stop loss: median ₹3,745, 90th percentile ₹7,932,
max ₹12,528. Account halt: ₹15,000. SuperTrend `max_lots = 10`, `risk_pct = 1.0`,
`max_daily_loss_pct = 3`.

**Options.**
- **A (recommended):** set Max lots to 2 in KITE → Tools → Connect → Trading Config. Worst
  normal 2-lot stop ≈ ₹15,900, so one bad trade halts the day, which is the intent.
- **B:** keep 10 and rely on risk sizing (`risk_pct = 1%` of F&O capital). Size is limited by
  capital, so the cap rarely binds; document that 10 is a ceiling, not a target.

**Optional code guard.** In the SuperTrend readiness check (`kite_engine/service.py`, section
"6b. Account-level daily-loss limit"), warn when `max_lots × median one-lot stop` exceeds the
account hard halt.

---

## 18. Strategy evidence

Research tasks, not code fixes. Before enabling AUTO with real money for a strategy:

1. Run the out-of-sample check and the random-entry comparison (same exposure, random entry
   times) for that strategy and mode.
2. Require a positive out-of-sample result and p < 0.05 against random entry.
3. Record the result under `docs/evidence/`.

Known state: Gamma Unwind's entry trigger has no edge (its level filter does); directional
Snapback failed out of sample (the market-neutral version with a futures hedge passed);
SuperTrend shorts net zero (`allow_short = False`).

---

## 19. Practice-only strategies

EMA Trend is practice only; Adaptive Edge places no orders. Both by design.

**Change.** In `frontend/src/components/kite/board/StrategyGuide.tsx`, add a visible
"Practice only — no real orders" tag for `ema_trend` and `adaptive_edge`, and hide the Buy
column on their boards. Optionally hide both tabs by default for new users
(Experience → signal-board tab preference).

---

## 20. Snapback quiet period

No code change. Weekly check:
```bash
curl -s -H "Authorization: Bearer <token>" "http://127.0.0.1:8000/api/v1/config/snapback/history?sessions=30"
```
If there are no signals for 30 sessions, review the stretch threshold and the market filter
in Snapback settings before changing the strategy.

---

## 21. Old worktrees holding databases

`~/Sterling-pr195-protection`, `~/Sterling-runtime-1.5`, `~/Sterling-safety-audit-20260929`
contain no unmerged commits but do contain `.db` files.

**Steps.**
1. Back up each worktree's databases into its own folder (the names collide):
   ```bash
   for w in Sterling-pr195-protection Sterling-runtime-1.5 Sterling-safety-audit-20260929; do
     mkdir -p /mnt/OS/SterlingReadinessBackups/worktree-dbs/$w
     find ~/$w -name '*.db' -not -path '*/node_modules/*' -exec cp -v --parents {} /mnt/OS/SterlingReadinessBackups/worktree-dbs/$w/ \;
   done
   ```
2. Remove the worktrees:
   ```bash
   cd ~/Sterling
   git worktree remove --force ~/Sterling-pr195-protection
   git worktree remove --force ~/Sterling-runtime-1.5
   git worktree remove --force ~/Sterling-safety-audit-20260929
   git worktree prune
   ```

**Done when.** `git worktree list` shows only `~/Sterling`, and the backups exist.

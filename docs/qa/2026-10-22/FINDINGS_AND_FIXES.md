# Confirmed findings, exact fixes and acceptance tests

> Remediation update: application fixes are now implemented locally. See [FIX_IMPLEMENTATION.md](FIX_IMPLEMENTATION.md) and [VALIDATION.md](VALIDATION.md) for current status; the audit below is the preserved pre-fix baseline.

Baseline: checkout/frontend `ff137155a083b2c52981545ff94400764ad6c188`. All locations below refer to that checkout. **No application fixes have been applied.** Backend behavior reproduced with test fixtures is not represented as an observed real broker transaction.

P0 = release identity/certification blocker; P1 = entry, protection, exit or material truthfulness defect; P2 = usability/documentation defect. Observations needing investigation are identified explicitly.

## QA-01 — P0: frontend and running backend have different release identities

**Evidence:** `evidence/release-identity.json`: published frontend/check-out `ff137155…`; running Python environment `STERLING_RUNTIME_SHA=9e989dd9…`. The latter is not a resolvable local commit. A current Python working directory does not prove that already-imported code changed with Git.

**Impact:** Current source/unit results cannot certify the loaded broker authority. Frontend-only publication cannot deploy Python fixes.

**Exact fix:** Select a versioned backend release and record its immutable source manifest. Expose startup-loaded backend SHA, frontend build SHA and schema compatibility in one read-only diagnostics response and show mismatches in Operations. Use the operator's established backup/reconciliation and controlled backend release procedure. Do not change a SHA environment label to disguise old loaded code, and do not restart a process with unmanaged exposure simply to make labels agree.

**Acceptance:** Restart/reconcile a controlled test deployment, then query identity from the actual process. The frontend and backend must match the approved manifest or an explicitly tested compatibility pair. Re-run admission, order, protection, reconciliation and journal tests for that release. Store request/response and build identity with the session artifact.

## QA-02 — P1: every SuperTrend entry freshness check assumes 60 minutes

**Location:** `backend/app/services/kite_engine/service.py:747`, especially `- 3600` in the closed-bar age expression. Auto-entry call sites include lines 865 and 1211; manual entry uses the same helper.

**Reproduction:** `backend/tests/test_trader_qa_20261022.py::test_characterize_fresh_completed_candle_admission`. A bar closed 30 seconds ago is refused for 1/3/5/15/30m, while the otherwise equivalent 60m bar passes. All eight non-hourly mode/interval combinations in the test return `stale_or_unclosed_signal`.

**Impact:** Fast-mode automatic entries are blocked at the wrong time. An old fast-mode row may later pass an hourly-sized age window. Manual fallback can convert this refusal into QA-03.

**Exact fix:** Derive the interval from the row's validated `horizon_mode` and `signal_timeframe_minutes` using `mode_execution.timeframe_minutes`. Compute `close_at = signal_at + timedelta(minutes=interval)`. Require a completed bar, current authorized session and a documented interval-specific TTL; never assume an hour. Keep calendar admission separate from bar-finality validation so callers cannot bypass both with one AMO flag.

```python
interval = timeframe_minutes(row.horizon_mode, row.signal_timeframe_minutes)
close_at = signal_at + timedelta(minutes=interval)
age_s = (now - close_at).total_seconds()
if age_s < 0 or age_s > ttl_seconds_for(interval):
    return "stale_or_unclosed_signal"
```

`ttl_seconds_for` must be one reviewed shared policy, not a new unbounded tolerance. Preserve the existing intended hourly TTL unless deliberately revised.

**Acceptance:** Every supported interval passes immediately after closure; forming/future bars fail; one tick beyond TTL fails; prior-session rows fail regular entry; wrong mode/interval fails; both spot and derivative sources behave consistently. Assert zero broker calls on every refusal.

## QA-03 — P1: stale/unclosed manual signals silently become AMO

**Location:** `backend/app/services/kite_engine/service.py:296` chooses any matching row after no fresh row exists. Line 346 skips revalidation for explicit AMO and converts **any** nonempty block reason to `variety='amo'`. Submission at lines 357–388 carries `allow_amo=True`.

**Reproduction:** `test_characterize_stale_manual_signal_becomes_amo[regular/amo]`. Pin the helper to return `stale_or_unclosed_signal`; a mocked live manual BUY still returns `ok` and the downstream order receives `variety=amo, allow_amo=True`. All other gates are deliberately satisfied by fixtures. No real broker call occurs.

**Impact:** Freshness and closed-bar failures change the financial instruction into next-session exposure rather than rejecting it. This is not merely a market-hours fallback.

**Exact fix:** Remove the unrestricted row fallback and automatic variety conversion. Bind plan selection to exact signal ID/generation, source, mode, interval and contract. Preserve `stale_or_unclosed_signal` as a terminal pre-submit refusal for regular entry. Explicit AMO needs its own reviewed validation policy: permitted signal session, completed bar, next tradable session, expiry survival, account/risk approval and protection ownership. For an immediate release, refuse AMO strategy exposure until that policy exists; transport testing remains possible offline. If AMO is retained, freeze the position horizon against actual execution/next-session policy rather than pretending submission time is fill time.

**Acceptance:** A stale/unclosed signal sends no POST for regular or AMO. Unknown calendar and expired/next-open-expiring contract fail. A valid explicit AMO sends once, is labelled queued and remains unfilled/unprotected until confirmed. Replayed request cannot create a second intent. Timeout stays UNKNOWN and is reconciled without resend.

## QA-04 — P1: Escape liquidates every owned Price Rules position

**Location:** `frontend/src/components/kite/PriceRulesPanel.tsx:54`, Escape branch at 62–66.

**Reproduction:** `frontend/src/components/kite/__tests__/traderQa20261022.test.tsx`. Render two owned open rules; dispatch Escape to `document.body`; two `/api/v1/price-rules/positions/{id}/exit` requests are sent with `{percent:100}`. No confirmation is requested. Inputs/buttons are excluded, but an ordinary page-focused Escape still exits all positions.

**Impact:** A standard dismissal key causes financial exits; a trader trying to dismiss a menu or overlay can liquidate a portfolio.

**Exact fix:** Remove the Escape exit branch. Reserve Escape for dismissal. Offer an explicitly named “Exit all Price Rules positions” control that previews exact account, contracts and remaining quantities, and asks for a deliberate confirmation. Keep the backend's existing serialization and owned-quantity bounds.

**Acceptance:** Escape from body, row, menu, overlay and input sends zero exit requests. An explicit confirmed action sends one request per selected owned remainder; cancel sends none. Partial/UNKNOWN outcomes remain visible.

## QA-05 — P1: calculator and copied artifact invent a stop and R ratio

**Location:** `frontend/src/components/kite/AdaptiveEdgePositionCalculator.tsx:67` uses `baseEntry*0.8` without a strategy SL; line 122 computes R against it; line 180 copies the invented level. `BoardTicket.tsx:113` passes missing values through as undefined.

**Browser evidence:** BRITANNIA Gamma WATCH plan: reference SL/TSL absent, calculator input SL ₹29.15 for entry ₹36.45, “No strategy stop supplied,” full premium risk ₹4,556.25, yet +₹1,825.00 / **2.00R**. See `07-gamma-plan.jpg`.

**Automated reproduction:** Entry ₹100, target ₹140, no default SL: the UI says no strategy stop but Copy Plan includes **Stop Loss ₹80.00**.

**Exact fix:** Represent absent stop as `null` end to end. Show SL/TSL and R as unavailable unless a real strategy level or explicitly labelled hypothetical assumption exists. Copy Plan must use the same authoritative data as the reference plan and include its provenance/status. Full-premium exposure may be shown as maximum premium at risk, but not combined with a stop-based 2R claim.

**Acceptance:** No missing-SL display, clipboard payload or export contains a fabricated numeric stop or R. A real stop produces consistent risk, target and copied values. WATCH/reference-only plans stay non-executable.

## QA-06 — P1: editable plan inputs do not control strategy-owned execution

**Location:** `AdaptiveEdgePositionCalculator.tsx:73` onwards; `board/GammaMoveBoard.tsx:678` and 694; `board/SnapbackBoard.tsx:322` and 339; `hooks/useSnapback.ts:455`.

**Evidence:** Calculator lots/entry/SL/target are local state. Gamma execution submits the candidate ID; Snapback publishes the signal ID and arms the server revision. The server properly owns those strategies, but the UI presents a single editable “Position Sizing & Trade Plan” beside a separate server-owned Buy action.

**Impact:** A trader can believe edited lots or stops will execute even though the server uses a different quantity/plan. This is a UI-to-execution contract defect; no incorrect real fill was observed.

**Exact fix:** For `allowOrder=false` with a strategy-owned `executionAction`, make authoritative plan fields read-only. Alternatively place editable fields in a clearly separate hypothetical calculator and show an immutable final server preview, including revision, quantity and stop, directly on the action. Any supported customization must publish a new validated server plan revision before arming.

**Acceptance:** Changing a hypothetical field cannot change the displayed executable plan. The button, confirmation, request, journal and broker payload all refer to one quantity and one plan revision.

## QA-07 — P1: manual ticket has a second, incompatible live protection workflow

**Location:** `frontend/src/components/kite/OrderWindow.tsx:278` creates pending browser GTT protection on order success; `PendingGttProtectionWatcher.tsx:31` posts it on COMPLETE. The live manual path already owns protection in `backend/app/services/kite_engine/service.py:357`. Generic live GTT creation is intentionally refused in `backend/app/api/v1/endpoints/kite.py:1637`.

**Impact:** Ticking SL/Target in a live signal ticket can schedule a client request that the backend will refuse, while the engine uses its own plan stop. The user's edited percentages are not included in `buildOrderBody`. The client can subsequently warn “Protective GTT failed” and suggest a manual stop even if canonical protection exists.

**Exact fix:** Return `protection_owner`, intent/position ID, pending state and authoritative plan in the canonical response. Skip browser pending-GTT creation for engine-owned live orders. Hide or lock generic SL/Target percentage controls for that path; direct operator-defined rules to the Price Rules strategy. Keep generic live GTT refusal intact. A desktop tab must not be responsible for protecting a server-owned position.

**Acceptance:** A canonical manual option fill creates at most one owned protection intent; browser close/reload does not lose management; no generic `/kite/gtt` POST occurs; user-visible SL/target equals the server's validated plan. Partial fills protect only filled units.

## QA-08 — P1: acknowledgement alerts conflate pending protection with failure

**Location:** `frontend/src/hooks/useKite.ts:571`; manual response in `backend/app/services/kite_engine/service.py:389`.

**Evidence:** Canonical manual response deliberately returns `protected:false, protection:'awaiting confirmed fill'`. The hook labels that “UNPROTECTED” and adds “No automatic exit”. The manual response also omits the `amo` flag that this hook relies on, even when the request variety is AMO.

**Exact fix:** Use an explicit lifecycle enum: QUEUED_AMO, ACKNOWLEDGED, PARTIAL_FILL, FILLED_PROTECTION_PENDING, PROTECTED, EXIT_PENDING, CLOSED, REJECTED, UNKNOWN. Return actual order variety. Infer neither fill nor protection from HTTP 200. Reserve “Protection failed” for confirmed owned filled exposure whose protective state failed. Do not automatically suggest another stop when ownership is unresolved.

**Acceptance:** AMO response shows next-session queue; accepted/unfilled shows fill pending; a partial fill shows its protected remainder; verified GTT shows protected; UNKNOWN directs reconciliation without retry. Toast and Orders/Positions/Journal agree.

## QA-09 — P2: Snapback advertises after-market execution that its authority refuses

**Location:** `frontend/src/components/kite/board/SnapbackBoard.tsx:228`. Server admission in `snapback_dispatcher.py:162` and `snapback_live_wiring.py:981` appends a market-session blocker. `snapback_plan_producer.py:165` relaxes one signal-date check for manual AMO windows but does not implement a complete broker-AMO lifecycle.

**Browser evidence:** “MARKET CLOSED · you can still place an after-market order” is displayed in the actual Snapback board. Current rows are historical/ineligible; no live click was submitted.

**Exact fix:** Remove this promise and show the server's current entry blocker. Add `supported_varieties`/`next_session_entry_supported` capability to the strategy response if AMO is genuinely implemented later. Do not remove shared session gates to make the caption true.

**Acceptance:** Closed-session UI never advertises an unavailable execution mode. Supported AMO, if introduced, has separate entry, reconciliation, protection and next-session deadline tests.

## QA-10 — P2: maximized Signals hides the selected navigation destination

**Reproduction:** Maximize Signals, then Tools → Connect or click an underlying's detail button. The visible pane remains Signals; restoring the workspace reveals the chosen main page. Native row expansion does work. This also made the LIVE status navigation appear ineffective during the audit.

**Location:** `frontend/src/components/kite/KiteTab.tsx:145` navigation changes; `KiteLayout.tsx:373` main-focus listener sets mobile pane but does not clear desktop Signals focus. Replay already explicitly clears another pane's focus around line 427.

**Exact fix:** On an explicit main-page/detail navigation event, restore the main pane if minimized, clear focus when a different pane is maximized, and then reveal/focus the destination. Reuse the existing replay restoration behavior; avoid an effect loop that overwrites user focus on every render.

**Acceptance:** From each half/max/full-screen pane, Dashboard, Connect, Operations, Positions and signal detail become visibly active after one navigation action; restore retains the prior layout.

## QA-11 — P2: static market-event data is labelled live

**Location:** `backend/app/api/v1/endpoints/kite.py:1157` IPOs and subsequent corporate-actions endpoint return literal May/June records. `KiteDashboard.tsx:185` labels them “LIVE IPOS”. Economic/Earnings buttons have no state handlers in that component.

**Evidence:** On 22 October the actual dashboard lists the same May/June records, including “Susan Electricals India”. These are source literals; no external claim about the real issuers is needed.

**Exact fix:** For this release, return a truthful unavailable/empty dataset with `source`, `as_of`, `stale`, and a reason, and remove the LIVE label. Alternatively connect a verified data source and filter by full dated status. Disable or implement the two nonfunctional calendar tabs.

**Acceptance:** No fixture record appears in production as live; stale data is dated; empty/unavailable is distinct from zero events. Calendar tab labels match actual content.

## QA-12 — P1 QA gate: two frontend mocks prevent 18 meaningful tests from running

**Location:** `frontend/src/components/kite/__tests__/engineTabVisibility.test.tsx:48` and `SterlingKiteEnginePane.rescan.test.tsx:76`. Both mock `useKite` with only `useKiteQuote`; the board now renders `KiteSessionBar`, which imports `useKiteStatus`.

**Evidence:** Existing full suite: 18 failures, all citing missing `useKiteStatus` export. Other 2,222 tests pass. This is not proof that 18 app behaviors are broken.

**Exact fix:** Add the minimal realistic connected `useKiteStatus` result to both mocks, or partially mock the module and deliberately supply all rendered hooks. Rerun those files, then the full frontend suite. Resolve any newly exposed assertions rather than treating them as pre-existing noise. The `window.confirm` jsdom warning should also be stubbed deliberately in the account-selection test.

**Acceptance:** All 18 tests execute their intended navigation/scan assertions and pass. New tests cover a disconnected session as well as connected state.

## QA-13 — scope/discoverability limitation: Simulation access is separate from strategy arming

**Location:** `frontend/src/components/sim/StrategyModeSelector.tsx:107` intentionally filters choosing SIMULATION unless already selected. The actual workspace route is `SpectrumNavigation.tsx`, whose destination visibility comes from Experience preferences and whose availability is owner-scoped. The older `ViewSwitch` is unmounted but is not the only route.

**Evidence:** This session had Show Simulation disabled. Temporarily enabling visibility did not make cycling select it under the current availability state. The original preference was restored and verified as off. Actual Trading Config offered MANUAL/ALGO; offline Simulation tests passed. This is an access/discoverability limitation, not a proven missing implementation or an authorization defect.

**Exact improvement:** Show an explicit owner-authorized Simulation destination and explain whether it is hidden, forbidden or unavailable, instead of silently skipping it in cycle navigation. Separately document whether this release permits switching a strategy into virtual automatic execution. Do not mount a second conflicting switch or bypass the existing owner check merely because the legacy component is unused.

**Acceptance:** A permitted owner with the destination selected can enter/leave virtual view without a real-account write. A forbidden or unavailable destination has a clear reason. Nonowners cannot access another owner's virtual book. Price Rules remains explicitly live-only unless separately implemented. The requested all-modes browser journey remains unverified for this session.

## QA-14 — P2: operator documentation disagrees with runtime defaults and paths

**Locations:** `EXECUTION.md` says unset live execution is true, but `backend/app/core/lane_registry.py:48` fails closed and README says disabled. `docs/operator/START_HERE.md` tells users to invoke `./sterlingctl` from the repository folder; the tracked executable is `scripts/sterlingctl`.

**Exact fix:** State that unset/blank/invalid live-entry configuration blocks new exposure. Use `./scripts/sterlingctl` from the repository root, or deliberately provide/test the documented launcher. Generate the strategy/mode/default table from shared configuration to prevent further drift.

**Acceptance:** Copy/paste every documented command in an isolated environment; help/status are reachable. Unset/false/invalid settings are documented and tested consistently, while exits continue independently.

The Trading Config field “Maximum lots per automatic entry” also says it does not size manual orders, while `service.py:315` rejects manual quantity above `cfg.max_lots * plan.lot_size`. Rename the field/help to describe both consumers, or split manual/automatic caps explicitly and test them. Do not make the hint true by removing the manual risk limit.

## QA-15 — P2: readiness and signal labels are easy to misread as permission to trade

**Location:** `frontend/src/components/kite/KiteFooterStatus.tsx:32` starts with “Live trading. New entries allowed” and only considers a subset of admission state. Signal rows use Open/Closed; footer uses “live” setup counts; board says ALGO while Trading Config says MANUAL. Initial narrow dock hides the distinguishing columns.

**Exact fix:** Label real-account mode separately from effective entry readiness. Use explicit `new_risk_allowed` and current blockers, with market session and per-strategy eligibility. Rename counts to active setups, distinguish setup ended from position closed, and spell out automatic-entry ON/OFF. Keep unavailable/unknown states visibly unknown. Provide a trading layout with readable minimum widths, fixed contract/action columns and a clear restore control. Use at least a practical readable base font and verify contrast quantitatively before changing color tokens.

**Acceptance:** With zero owned holdings, 25 active setups cannot look like 25 broker positions. A closed market or failed admission never has an unconditional “new entries allowed” hint. MANUAL has no ALGO-on implication. At desktop/tablet/mobile sizes the exact contract, quantity, SL and action remain legible.

## QA-16 — runtime investigation: incomplete Gamma recovery and post-close timestamps

**Observed:** Actual Gamma status: “historical watchlist recovery incomplete: NHPC”. SuperTrend includes a NIFTY ultra-scalp signal stamped 15:41; expanded Gamma quote detail reports an LTT after the regular-session close. The timestamp may reflect upstream/post-close publication or processing, not a tradable candle.

**Scope:** These are real displayed anomalies, not a proven loss, a proven feed fabrication, or a diagnosed code root cause. Funds/IP are unrelated exclusions.

**Exact next steps:** Capture bounded per-symbol recovery errors with exception type, source, attempts and last successful source timestamp. Distinguish incomplete historical reconstruction from a current market-feed outage. Trace each anomalous signal to the raw candle's start/end, exchange session, timezone, vendor timestamp and ingestion timestamp. Display data-as-of separately from signal close. Reject entry data outside the authorized continuous session, without rejecting valid later publication of an earlier closed candle.

**Acceptance:** Replay a known session and prove every executable signal references a completed in-session bar and a permitted source. Recovery can be partial without hiding healthy rows, but incomplete inputs never become execution-eligible by default. Include data provenance in exported evidence.

## QA-17 — P1: Gamma guide promises profit booking at T1 that is not implemented

**Location:** `frontend/src/components/kite/board/StrategyGuide.tsx:11` says “Takes profit at +20% and +40%.” In `backend/app/services/gamma_move_runner.py:2031`, T1 changes state to `T1_TRAIL` and ratchets the stop to entry. The explicit comment at line 2058 refuses to invent an unspecified partial-booking fraction. `backend/app/engines/gamma_move/exit.py` declares the same behavior.

**Impact:** A beginner can believe +20% profit was realized and continue holding based on money that was never booked. The implementation may be intentionally faithful to an incomplete reference, but the user guide is factually inconsistent with it.

**Exact fix:** Change the guide to “At +20%, moves the stop to entry. At +40%, exits.” Identify the current implementation in the reference notes and notifications. If partial profit booking is a future product decision, define lot-safe fractions and residual protection before implementing it; do not choose an arbitrary fraction during a QA patch.

**Acceptance:** A T1 test asserts zero SELL orders and one confirmed stop transition; guide, alert and journal say breakeven/trailing, not realized profit. At T2 or an actual partial fill, realized quantities and P&L are derived from confirmed trades.

## QA-18 — P1 reporting gap: Price Rules cannot appear in the global Trade Journal

**Evidence:** The actual Trade Journal says it records every trade but its strategy filter offers Supertrend, Snapback, Gamma Unwind and EMA Trend. Price Rules is absent. `backend/app/services/trade_journal.py:54` excludes `price_rules` from `STRATEGIES`; `record_entry` at line 650 rejects unknown strategies. `price_rules.py` records durable order intent/fill state but has no global trade-journal adapter. Its separate CSV does not repair a missing global reporting projection.

**Impact:** The fourth primary live strategy is missing from the journal's lifecycle story, strategy statistics and unified export/reporting contract. This finding does not mean its durable broker-order journal or owned exits are missing.

**Exact fix:** Add Price Rules to the global strategy schema, narrative/filter/statistics definitions and a lifecycle adapter. Join rule/revision/account to entry intent/order/fills, owned stop/child, partial exits and final realized outcome using stable identities. Project idempotently from confirmed durable facts; backfill only facts actually present in the rule store, preserving unknown fees/timestamps. Keep the existing execution journal intact.

**Acceptance:** A Price Rules draft/arm/fill/protect/partial-exit/close scenario appears once in the global journal and API, survives restart without duplicate trades, is selectable in the UI and reconciles exactly with its dedicated CSV. Unknown or unresolved fills must not become a fabricated closed trade. Include Price Rules alongside the existing three-strategy journal E2E cases.

## Exact regression targets after repair

The two newly added QA files are **characterization tests that currently assert the observed defects**. Do not use their green status as release approval. After fixes, change them to assert:

1. Each completed supported interval is admitted within policy TTL.
2. Stale/unclosed/manual mismatches cause zero order POSTs for every variety.
3. Escape causes zero exit requests.
4. Missing SL stays absent in screen, clipboard and export; R is unavailable.
5. Intercepted valid explicit AMO still uses the documented form and cancellation route.

Add meaningful cross-layer regressions for single protection ownership, exact server plan review and state-specific notifications. Preserve the currently passing partial-fill, unknown-outcome, fired-child, exact-owned-exit, restart and journal cases.

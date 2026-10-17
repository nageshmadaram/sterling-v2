# EMA Trend validation report

Date: 2026-10-17. Scope: source implementation and hermetic paper/replay verification. No real-money order, live deployment, real account allocation, performance promotion, or production database restore was performed.

## Results

| Check | Result | What it proves |
|---|---|---|
| Strategy core + durable runtime + scoped API | 162 passed | Indicators/setups, lifecycle/risk, CAS/ownership, real virtual broker/canonical order/fill ledger integration, recovery, backup/restore |
| Related backend regression set | 193 passed | Legacy simulation modes, journal, frozen Supertrend parity, unified dispatch and uncertain execution handling |
| Focused frontend and shared-row parity | 61 passed | Settings/hook contracts, equity adapter, native backtest, detail actions, existing row metrics |
| Browser contract acceptance | 10 passed | Four profiles on desktop and mobile: Connect → scan → shared row → chart → paper plan → filled shares → quarter reduction → close → replay |
| Production frontend build | PASS | TypeScript and Vite build; existing large-chunk warning remains |
| Whitespace patch check | PASS | `git diff --check` |

Browser tests use intercepted API fixtures and actual production UI components. They are not a connected-provider or real broker execution test. Backend integration separately exercises the actual VirtualExchange, canonical order journal and fill ledger. Screenshots are stored by browser project, profile and workflow stage under `frontend/test-results/ema-trend/` when the browser command runs; that directory is generated test output and may be cleaned by later Playwright runs.

The final review found that the simulator's public Kite-shaped trade response strips private virtual charges/timestamps. The projection now obtains these modelled facts from the authenticated concrete virtual ledger, checks its trade quantity against cumulative order fills, and tests exact fees and first-fill timestamp. This prevents falsely marking zero fees as complete.

Known terminal failed/incomplete sell reductions conservatively escalate to CLOSING/RISK_EXIT. Reconciliation alone never resubmits the failed ordinary reduction. UNKNOWN remains distinct. The optional one-successor retry optimization in the plan has not been implemented.

## Reproduce

From the repository root:

```bash
backend/.venv/bin/python -m pytest backend/tests/engines/ema_trend backend/tests/services/test_ema_trend_runtime.py backend/tests/api/test_ema_trend_api.py backend/tests/api/test_ema_trend_family_operations.py -q
backend/.venv/bin/python -m pytest backend/tests/unit/simulation/test_tenancy_mode.py backend/tests/unit/test_trade_journal_store.py backend/tests/unit/test_supertrend_parity_freeze.py backend/tests/api/test_unified_backtest_api.py backend/tests/unit/test_execution_unknown_settlement.py backend/tests/unit/test_family_operations_v2.py -q
git diff --check
```

From `frontend/`:

```bash
npx vitest run src/components/__tests__/EmaTrendSettings.test.tsx src/hooks/__tests__/useEmaTrend.test.tsx src/components/backtest/__tests__/EmaTrendBacktestPanel.test.tsx src/components/kite/board/__tests__/emaTrendAdapter.test.ts src/components/kite/board/__tests__/EmaTrendTicket.test.tsx src/components/kite/board/__tests__/EmaTrendOperations.test.tsx src/components/kite/board/__tests__/rowStyleParity.test.ts src/components/kite/__tests__/LiveOperationsPanel.test.tsx
npx playwright test --config playwright.ema-trend.config.ts
npm run build
```

Browser prerequisites: the project's Playwright Chromium installation. The isolated harness starts Vite on loopback port 5188 and does not require production login/broker credentials. The full repository suite has not been run.

An additional Connect settings-hub regression run had 17 passing checks and one failure in the unrelated broker-account onboarding test, which still expects `+ ADD KITE ACCOUNT`. The current account UI differs from that legacy expectation. It was not hidden or treated as passing. Initial session-storage contamination/stale legacy navigation-key tests were corrected; a shared-row origin badge was scoped to EMA to preserve other strategy styling. Current focused parity tests pass.

## Evidence and acceptance limits

Saved test logs under `validation/` contain the command output and focused frontend JSON summary. No log is a profitability report. No actual market dataset was economically accepted here. OHLC replay cannot establish queue/fill realism, especially for Ultrascalp. Calendar coverage, daily execution proxy, paper fees, process-dependent protection and incomplete production acceptance are detailed in IMPLEMENTATION_STATUS.md and OPERATOR_GUIDE.md.

A14 is BLOCKED/INCONCLUSIVE. Full A00–A13 acceptance is not claimed: deployment-wide identity/dataset provenance parity, corporate-action/settlement handling, generic global ops integration, independent operator recovery/usability drills and connected-provider operational evidence remain outstanding. Live remains unavailable regardless of UI switches or backtest result.

## Follow-up: audit lineage and paper operations

New repository documents and events receive server-generated origin metadata with config revision/hash, universe hash, profile, tenant/account, pure-rule hash and a hash of strategy engine/runtime source files. Updates preserve the original binding and record the current projection source separately. Config revisions receive a fresh config identity. Children inherit setup/plan/campaign origin; old records lacking provenance remain explicitly unverified, including descendants. Caller-supplied provenance cannot set owner or live eligibility. The source digest is scoped to strategy engine/runtime files, not a claim of whole-build/deployment attestation. Existing database records are not retroactively certified.

The always-visible paper operations panel aggregates all profiles independently of row filters, requires an explicit second click to request close-all, and disables actions when state is unavailable or historical. The API pauses first, attempts each owned campaign, preserves other owners and reports individual unresolved failures. New tests cover origin preservation across config changes, legacy identity refusal, all-profile shares, action confirmation/cancellation, read-only guards and tenant-safe partial failure.

## Follow-up: recorded dataset audit

The shared dataset module hashes canonical serialized bars together with symbol and interval; a pair hash binds signal and context datasets. It records observed bounds, availability, provider/adjustment lineage, calendar-policy scope and detected quality issues. Missing expected candles are counted with bounded timestamp samples. Scans store immutable content-addressed manifest documents and attach IDs/hashes to signals and plans; origin lineage carries the dataset hash into campaigns/events. Replay runs and native reports include the same audits, displayed in the evidence UI and retained in saved-record export.

The audit checks only observed bounds, not completeness of a provider request. It reports, rather than repairs, gaps and unknown calendar days. Weekly manifests explicitly use PARTIAL_CHECKS when no other issues are found; constituent coverage still needs upstream evidence. Corporate actions and execution microstructure are not certified. Audit statuses do not grant live/economic eligibility. New tests exercise exact-hash reproducibility, price/provider/context changes, missing candles, late availability, calendar uncertainty, bad ordering, persisted scan links/export and visible inconclusive UI output. The authenticated read-only `/api/v1/ema-trend/datasets/{dataset_id}` endpoint permits owner inspection and rejects cross-owner reads; there is no mutation route.

## Follow-up: handover and supervision

The runner now persists a throttled completed-cycle heartbeat and reported errors; snapshots/export expose UNKNOWN, RUNNING, DEGRADED or STALE state. The UI warns about stale/missing/error supervision while retaining emergency close controls. Tests exercise successful and failed actual runner iterations, persistence throttling, tenant isolation, staleness and recovery. This monitor is part of the backend, not an independent external watchdog.

New buy admission checks originating pure-rule identity in both planning and order submission. Missing or mismatched rule identity blocks an entry/add without submitting a new order. Canonical sells remain available; a rule-change test closes positively owned shares. START_HERE.md and READINESS_REPORT.md provide a finite handover and distinguish verified paper software from outstanding connected/live/economic acceptance.

## Follow-up: application Operations fanout

The authenticated Operations endpoint now appends the owner’s EMA virtual campaigns and supervision in a dedicated paper_strategies section. Real broker inventory and live admission verdicts are unchanged. Unreadable paper sources return UNAVAILABLE with null inventory/supervision rather than a falsely empty book. Simulation identities are normalized to their authenticated owner; payload/query parameters cannot select another owner.

The application Operations UI renders dedicated EMA paper pause/resume/close-all controls and invalidates both strategy and Operations caches after a mutation. Existing Snapback family-stop scope is preserved. Backend outage demotes cached EMA supervision to UNAVAILABLE and disables operations; historical replay suppresses paper actions. Tests cover endpoint ownership/isolation, unchanged real broker projection, source failure, actual virtual inventory read-only behavior, dedicated mutation routes and cached-state outage handling. Browser regression remains the four-profile strategy journey; the application-wide panel is covered by API and React integration tests.

## Follow-up: actual instrument/contract selection and securities derivatives

Connect now searches the authenticated broker master and exposes exchange/contract filters, exact selections, expiry, lot and tick metadata. Unsupported results remain visibly blocked. NSE/BSE equities and NFO/BFO FUT/CE/PE flow through the same canonical paper service. The selected contract’s own bars drive the strategy; no underlying/premium substitution is made. Initial quantities, base quarters, manual reductions, fills and add-backs preserve lot steps. New campaigns require at least four lots, reserve full futures notional conservatively, fence changed metadata, and direct expiry exposure toward risk closure.

Independent sizing/lifecycle tests and actual virtual-exchange integration cover futures/calls/puts on NFO and BFO, including lot-safe quarter reductions and closure without real broker writes. Exact-contract replay refuses missing frozen metadata. Existing cash-equity fixtures continue to pass. NFO candle completion now follows the repository’s verified venue close rather than incorrectly labelling a futures daily bar complete at the cash-equity close.

MCX execution remains unimplemented and explicitly blocked because the current shared virtual broker lacks commodity multiplier/session/accounting support. Search visibility is not an execution capability. The requested full commodity expansion is incomplete. See INSTRUMENT_EXPANSION.md for the sequence and primary broker references.
# Production integration evidence follow-up

Live-transition prerequisites: 225 shared settlement/GTT/protection tests and 52 EMA runtime/API/acceptance/admission tests passed, plus 22 frontend tests and the production frontend build. Logs are saved as `validation/live-transition-{protection,core,frontend,build}.txt`. Shared protection tests exercise actual Kite transport against HTTP fixtures; acceptance-service tests use controlled broker responses. Neither is a connected live-market acceptance run. Counts overlap earlier suites. See `LIVE_TRANSITION_STATUS.md` for the remaining live adapter and protection integration work.

The campaign evidence endpoint and ticket export were verified with 45 passing runtime/API tests, including canonical virtual entry/reduction fill quantities and ownership isolation, and 5 passing ticket tests, including the read-only download. The frontend production build passed. Logs: `validation/production-integration-backend.txt`, `validation/production-integration-frontend.txt`, `validation/production-integration-build.txt`. These counts overlap earlier suites; do not add them to the earlier total. This follow-up placed no real-money orders and does not establish connected-market or live-release acceptance.


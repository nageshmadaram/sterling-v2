# Sterling strategy audit and implementation specification

Status: audit/specification written first; implementation completed; final validation ledger below.
Scope: SuperTrend, Snapback, Gamma Unwind; their signal boards, managed execution,
chart navigation, replay, help, position math, and terminal diagnostics.
Date: 11 October 2026 (IST). Source baseline: main, 1c604f95898209ae0e0b54b9cae8d10c53891670.

## Outcomes and limits

The delivery must make the requested workflows consistent, operational, and testable.
Correct execution and backtesting do not establish a profitable edge. No profitability
claim, guarantee, invented broker confirmation, or artificial weakening of a risk gate is
acceptable. Positive historical metrics must retain costs, data provenance, sample size,
and out-of-sample limitations. The implementation must preserve strategy ownership,
idempotency, confirmed-fill accounting, broker protection, and unknown-outcome recovery.
Production money movement requires configured account authority, security, risk budgets,
market data, and deployment evidence. This development instance has no live broker account.
Do not submit real orders while auditing or running automated checks.

## Audit evidence already available

The unchanged baseline built successfully. Local application, authentication, virtual
SQLite tenant, API proxy, and persistence across restart worked. All 294 data-lake tests
passed. Frontend baseline: 1,903 passed, two styling/source assertion failures.
Backend baseline: 10,487 tests executed; after a corrected isolated backup-suite rerun,
latest outcomes were 10,441 passed, 16 clock-probe failures, 30 skipped. The kernel
reports TIME_OK and no unsynchronized flags; timedatectl cannot report NTP status in
this container because PID 1 is not systemd. Baseline production clock verification
was not bypassed.

AGENTS.md asks for code-review-graph tools first. No such callable tools are available
in this task, so source inspection and existing tests are the necessary fallback.

## Verified findings and affected artifacts

| Request | Root cause / observation | Implementation boundary |
|---|---|---|
| Common collapsed signal table | SterlingKiteEnginePane defaults to the classic renderer through useKiteSettings. SuperTrendSharedBoard builds columns from persisted SuperTrend order; SnapbackBoard and GammaMoveBoard independently request BOARD_COLUMNS and use per-board hidden settings. Their default sets and ordering differ. | One canonical collapsed-column contract and persisted visibility/order for the three strategies. Shared renderer is the normal workflow; strategy-specific facts belong in expanded sections. |
| Chart buttons | KiteTab.handleOpenInstrument clears detailView and setupView but leaves boardDetail, whose rendering branch precedes instrumentView. SignalDetailPane.LegCard also installs an onChart handler that only stops propagation. | Clear every competing view and pass a real chart callback through detail components. Retain exchange/raw symbol and signal marker context. Restore the center dock. |
| Managed execution lock | Snapback and Gamma deliberately disable generic BoardTicket ordering, but their actual owned actions are buried below the calculator. Gamma's shared trade renderer explicitly returns null. The ownership notice is not itself an execution capability. | Put the existing managed publish/arm or Gamma arm action in the common Trade cell and expanded action area; keep the same eligibility verdict and server checks. Generic order forms must never originate their positions. |
| Portable clock gate | snapback_preflight._ntp_synchronised assumes timedatectl/systemd and ignores process return code. An unavailable bus becomes not_synchronised:unknown. Existing live-shaped tests then refuse otherwise valid mocked entries. | Add an actual read-only Linux kernel discipline probe as fallback when the systemd probe is unavailable. An explicit negative NTP answer, TIME_ERROR, unsynchronized/error flags, unsupported ABI, or syscall failure stays blocked. No fabricated result or skip flag. |
| Replay parity | simulation.py has a long generic replay pipeline and Snapback daily rule support, while Gamma Unwind has a separate gamma_move_historical reconstruction pipeline. Gamma fields exist in replay event schemas, but the generic runner has no Gamma dispatch branch. SuperTrend fallback evaluates replay bars separately from the live engine; timeframe/provenance must be audited before calling it strategy parity. | Connect supported strategy IDs, typed capabilities, causal event timing and existing strategy rule/lifecycle implementations. Require dated instrument identity, completed bars, and option/market data evidence. Explicitly report missing inputs; never invent profitable trades to fill a table. |
| Replay versus live board | AdaptiveEdgeRightSidebar renders live Gamma and Snapback boards while a shared replay clock advances; Snapback intentionally uses a wall clock. This can present live rows beside historical SuperTrend results. | Route all three boards to the active replay session's own snapshots. Historical rows remain read-only, and replay cannot invoke broker-mutating endpoints. |
| Explanatory text | SnapbackBoard contains evidence paragraphs, mode explanations and per-board details; GammaMoveBoard contains strategy/watch/record explanations. HelpPane already has dedicated strategy sections but only abbreviated instructions. | Move static explanations to Help; keep concise operational status/error information, execution blockers and coverage visible. Dynamic strategy evidence belongs in expanded details or a linked help/evidence section. |
| Position information | AdaptiveEdgePositionCalculator repeats movement in LTP and Points covered, repeats target R in header and reward, and separates each stop price from its rupee impact. Calculator quantity can be mistaken for a confirmed held position. | Group position/mark, protection/downside, and exit/target. Show each price with its associated rupee effect once. Distinguish planned quantity/estimate from confirmed broker holdings; retain signed money, missing values and execution assumptions. |
| Readable symbols | InstrumentLabel parses derivative names, but chart titles, action labels, logs, calculators, replay tables and other screens still render raw strings independently. | Central plain-text display formatter plus the existing React label. BANKNIFTY26NOV55200CE -> BANKNIFTY NOV 55200 CE. Retain raw identity in API payloads, keys, selections, and metadata. Weekly contracts retain the expiry day to avoid ambiguity. |
| Diagnostics | diagnostics.ts wraps console and fetch into a 500-entry client ring. EngineTerminal replaces activity with diagnostics, treats network messages as frontend, gives polling errors a new timestamp every render, and has no correlated export/capture session. Backend logs are global and server-logs currently accepts any user. | Bounded source-aware capture, stable IDs, HTTP method/status/duration/correlation, resource/error/rejection/stream failures, backend structured metadata, consistent source/severity filters, and redacted downloadable bundle. Administrator access for global server logs. Put capture controls beside Clear. |

## Implementation detail contracts

### 1. Common table and signal state

Canonical collapsed columns follow the SuperTrend reading order:
Instrument, exchange, contract leg/delta, Entry/movement, SL, TSL, Exit,
Target, day change, day change %, direction, LTP, Time, managed Trade, Chart.
Persisted column ordering and hiding must affect all three requested strategies.
A column without a valid value displays an em dash and does not disappear per engine.
Preserve common status and execution availability; put conviction scores, Gamma tiers,
OI/level gates, Snapback daily evidence, hedge details and strategy origin explanations
inside the expanded row. Reordering must preserve header/cell order.

Adapters must not conflate thesis direction with order side: a bearish long put still
has BUY entry. Parent rows are ideas, contract rows identify what trades. Historical,
ended, missing-identity, non-ENTRY/watch-only and stale/ineligible rows cannot acquire
broker authority through a display control. A running position's exit remains owned
by the original strategy and must remain available when new-entry gates halt.

### 2. Owned action flow

Snapback action calls useExecuteSnapbackSignal:
signal id -> server publish -> validated plan id/revision -> arm expected revision
with stable idempotency key -> strategy dispatcher -> risk/admission -> broker ->
confirmed fill/protection -> state/journal. A stale plan must be republished and
revalidated rather than armed with an invented revision. A rejected response displays
the actual reason and must not look successful.

Gamma action calls useArmGammaMove and the existing server arm authority.
Only eligible ENTRY-tier signals can originate exposure. Preserve supported live
intraday expiry, entry cutoff and broker/both stop requirements. Watchlist, historical
reconstruction and unsupported swing protection never use a generic BUY fallback.

SuperTrend manual/automatic entry and exit ownership must be traced through the
engine endpoint and the canonical order router, including protection and replay
boundaries. Existing generic helpers must not make a strategy appear managed unless
the backend actually owns its fill, stop, reconciliation and exit.

### 3. Replay

For each selected strategy, trace configuration -> historical data -> indicator/setup
-> completed-bar signal -> contract selection -> adverse fill/costs -> protection
-> mark -> exit -> emitted snapshot -> UI board -> result export.

Mandatory invariants: no future bars/quotes/OI, no current instrument master silently
substituted for dated identity, no current premium used as a historical fill, no exit
computed from future bars at entry, no duplicate signals/trades after seek/restart,
no broker mutations, no missing input represented as zero, and consistent signal/trade
filtering. Switching the strategy or session clears stale state and displays the correct
session clock. Jump-to-end skips must remain distinct from evaluated bars.

SuperTrend must use the same 1H Heikin-Ashi/triple-SuperTrend settings and completed
bar semantics as its live logic. Snapback swing must preserve daily market filter,
next eligible session entry, holding horizon, hedge and costs; unsupported mode parity
must be made explicit. Gamma must use its actual historical E1/ENTRY rule and required
OI/option evidence, not reuse a generic trend signal under the Gamma label.
Where historical market data cannot be obtained, provide precise coverage diagnostics
and tests against deterministic retained fixtures; do not claim live-data replay validation.

### 4. Help and detail organization

Help owns entry/exit conditions, supported modes/timeframes, risk controls, expiry rules,
replay limitations, column glossary, diagnostic usage and broker prerequisites.
Signals retain compact status and a help affordance. Error/blocker information needed
for the next user action must remain visible and accessible.

Position summary: quantity/lots and planned-versus-filled identity, entry/capital,
current LTP and movement, gross marked P&L, separate estimated charges/net if available.
Protection: initial SL and defined loss, current TSL and profit locked/downside,
protection status. Exit plan: target and reward/R once, strategy-owned exit state/action.
All computations use the traded premium, actual chosen units and signed rupees.
A positive stop outcome is profit locked, not negative risk. Costs remain estimates
unless broker charges are known. A strategy's nonexistent fixed target remains absent.

### 5. Diagnostic capture and security

Use bounded in-memory rings, stable monotonic IDs, real timestamps and source metadata.
Never collect request/response bodies, credentials, Authorization/Cookie headers, full
login query strings, broker tokens, local-storage dumps or account databases.
Redact console/error strings and backend formatted exceptions before they enter rings
or an export. Backend records should preserve correlation IDs and source/logger,
with request timing/status events using the existing correlation context.
Polling diagnostics must not create an endless self-logging storm.

Enable capture from the terminal icon beside Clear. Expose frontend, network/streams,
backend and engine sources with matching severity filters. Allow a redacted JSON bundle
download with capture period and available/unavailable source status. A backend log
permission failure must be stable/actionable, not a new synthetic event every render.
Global backend logs require administrator authority.

## Validation and acceptance

1. Regression tests mount all three boards and compare actual collapsed column sets,
order, visibility and strategy-specific expansion. Tests check click behavior, not just
source-string formatting.
2. Chart tests cover navigation while shared detail, legacy detail and setup are open;
assert the exact exchange-qualified raw symbol reaches the chart.
3. Owned-action tests confirm exact signal id/revision/idempotency, eligibility reasons,
pending/rejection behavior, and inability of historical/watch rows to call generic orders.
4. Real-clock tests verify systemd yes/no, unavailable bus -> disciplined kernel,
unsynchronized/error kernel, unsupported platform and syscall errors.
5. Replay tests cover each supported strategy's signal and lifecycle, known warmup,
data gaps, temporal boundaries, replay snapshots, filters, seek and live-order exclusion.
Use existing meaningful suites and add causal/ownership regressions for new behavior.
6. Formatter tests cover monthly/weekly/FUT, exchange prefixes, hyphen/& and numeric
underlyings, malformed input, ambiguity and unchanged API identity.
7. Position math tests verify the user's example, missing/zero quantities, no target,
profitable/loss-making trail, charges and planned-versus-filled labels.
8. Diagnostics tests verify boundedness, redaction (including exceptions), source/severity,
HTTP correlation/timing, failing requests, capture/export, and admin-only backend access.
9. Run frontend production build and full unit suite; relevant backend suites followed
by the full regression suite. Run functional API/UI checks on this isolated machine.
Record pass/fail/skip and distinguish fixture checks from external broker evidence.

## Production and profitability evidence

A code-correct app cannot be certified profitable from this checkout alone.
Record data coverage, strategy version/parameters, round-trip charges/slippage, sample
size, drawdown, loss runs, and out-of-sample/walk-forward results. Keep historical,
theoretical, virtual and real broker evidence distinct. Do not select thresholds using
the same holdout used to claim success. Keep capital preservation and explicit limits
before optimizing returns. The family needs understandable operator instructions,
credential recovery and emergency/exit procedures, not an unsupported income promise.

Final handoff must list exact changes, artifact references, test outcomes, diagnosed
limitations, and any credentials/data/static-egress/security/risk prerequisites that still
prevent real-money production certification. No real trade or publication is authorized
by a successful mock/virtual test alone.

## Implementation and validation ledger

This section is updated during implementation with exact function changes, root-cause
evidence, regression results, and remaining limitations. The audit/specification is the
first tracked change; application changes follow it.


### Changes implemented against the contract

| Boundary | Artifacts and exact behavior |
|---|---|
| Table settings → each board | `useKiteSettings` version 12 migrates the renderer to shared. `useSignalColumns` maps the persisted SuperTrend left/right order and hidden columns for all three boards, including drag and sideways-scroll preferences. `SignalBoard.visibleColumns` respects the requested order instead of reordering through its private catalogue. Scroll mode preserves every requested column. Missing measurements remain dashes. |
| Collapsed → expanded | `strategyDetailsInExpansion` moves origin, Gamma gates, Snapback flags and SuperTrend exit/re-entry evidence to expansion. Duplicate labels across marks/flags are removed. Common identity, prices, timing and action cells remain in the same positions. Shared headings/cells have consistent geometry and accessible cell identifiers. |
| Trade authority | Snapback's Trade cell calls `useExecuteSnapbackSignal`, which publishes then arms the exact plan revision with idempotency. Gamma's Trade cell calls the existing arm endpoint and honors the actual entry gate. Expanded generic `BoardTicket` explicitly excludes Snapback/Gamma; generic signal entries offer BUY only for armed, eligible, identified contracts with a known lot size. They cannot sell to open. Confirmed owned exits remain in Positions and existing strategy/emergency paths. |
| SuperTrend manual ownership | The existing `POST /kite/orders` option-entry path delegates to `kite_engine.service.place_manual_order`; the live path resolves the exact account, checks order mode, admission and idempotency, and enters `_place_live_manual_buy` with protection/registration. No replacement broker router was added. |
| Systemd → actual clock | `clock_discipline.kernel_clock_synchronised` queries Linux `adjtimex` with modes=0 (read only). Snapback's clock probe uses it when timedatectl cannot answer. Supported ABI is checked; TIME_ERROR, unsynchronized/error flags, excessive error bounds and syscall failure remain refusals. An explicit systemd “no” remains a refusal. The baseline container's 16 clock-related test failures are resolved through actual clock evidence. |
| SuperTrend replay generation | `replay_strategy_views.hourly_prefix` delegates completed-bar handling to the production scanner. The runner hydrates 90 days of 1H history independently of playback resolution. `supertrend_entry` calls the production engine once per completed hour; no 5m HA substitute and no fabricated fixed 2R target. `canonical_entry_bar_ms` anchors lifecycle reconstruction. |
| SuperTrend replay lifecycle | `settle_supertrend` uses the production regime, entry transitions, `resolve_exit` and reported trail. It marks/exits only against the available 1H prefix and uses adverse raw-open handling on gaps. Dataset end does not falsely close a trend position. Option valuation/fills remain the simulator's theoretical delta model. This validates underlying signal-core reuse, not premium-source/derivatives/confluence execution parity or actual option returns. |
| Recorded SuperTrend → replay | Runtime archive loading is scoped to the requesting uid. A recorded candle-open timestamp becomes available at the candle close, capped by the exchange continuous close. `kite_snapshot` excludes other engines, future events and fabricated preference-only contract legs; it keeps the event's exact raw contract and recorded identity. Playback and review both report `market_open=false`, `is_replay=true`, `is_fresh=false`. |
| Gamma replay | `load_gamma_days` reads the user's retained E1 archive, or calls the existing historical reconstruction with an authenticated broker. Immutable E1 timestamps release observations causally; session-end watchlists/positions/record summaries are excluded. IDs plus timestamps deduplicate releases. Missing data has explicit coverage. With events but no OHLC, clock-only observations allow playback without inventing candles. Capabilities declare Gamma signal-only; no fabricated Gamma trades or P&L. |
| Snapback replay | Existing daily Snapback feature/filter/model/hedge/next-session lifecycle is retained and exercised. `snapback_snapshot` publishes its events and matching replay trades into the normal Snapback board. Unavailable setup-metric snapshots are marked and omitted from displayed evidence. Model identity never supplies a broker token. This shared replay is the swing lane; it does not certify scalp or research intraday economics. |
| Replay API → frontend clock | Snapshot routes switch to the session only for the owning uid. Gamma/Snapback query keys separate live versus replay/session; live date history is excluded during replay, and scan/arm controls are disabled. `BoardSignal.historical` prevents expanded tickets from fetching current quotes into past results. Premium ladders retain available premium values and leave missing premium stops/targets null rather than mixing underlying units. |
| Chart routing | `KiteTab.handleOpenInstrument` clears `boardDetail`, `detailView`, and `setupView`, restores the center dock and retains raw symbol/context. `SignalDetailPane.LegCard` now receives a real chart callback. Shared SuperTrend rows supply premium-leg signal markers. Snapback instrument names now also open detail. The global navigation listener is refreshed with current state so Help/close restores the saved terminal mode. |
| Instrument display | `lib/instrument` is the shared pure parser/formatter, re-exported by `InstrumentLabel`. Monthly, weekly, futures, exchange-qualified and numeric/hyphen/& underlyings are covered. Plain chart/detail labels, replay labels, calculator copies, Gamma history, data-lake browsing, Price Rules, terminal sentences and notifications use readable text. API/WS keys, payloads, archives and order identities remain canonical. Weekly dates retain the day; full expiry stays in detail metadata. |
| Position math | Calculator shows Position estimate, Protection and Exit plan. Entry/outlay, one LTP move, gross MTM/R, signed stop loss, trail downside/profit locked, target reward/R and estimated charges are grouped. Lot size must be supplied from instrument identity; the old guessed exchange lot constants are removed. A missing strategy target is never invented. Plan quantity is explicitly distinguished from filled holdings. `BoardTicket` passes the actual exit-progress text to the summary. |
| Help | Dedicated sections describe each strategy's setup, owned entry, exit, timeframe, supported live scope, replay provenance and diagnostics. Static Snapback prose is removed from the board; evidence moves into expansion. Operational failures and blockers remain visible. Gamma and Snapback link to Help. Existing modeled SuperTrend study limitations are included. |
| Browser → diagnostics | A bounded 1,000-entry ring captures console errors/stacks, uncaught/resource errors, unhandled rejections, fetch method/path/status/duration/response correlation and EventSource connection failures. Headers, bodies and query strings are excluded. Secret redaction happens before buffering/export. Export sanitizes values before JSON serialization, preserving valid escaped JSON. |
| Backend → terminal | Backend ring includes formatted/redacted exceptions, stable session/sequence IDs, correlation and structured HTTP metadata. Request capture bypasses logger-level suppression without collecting bodies/queries and excludes log polling. Global log API now requires administrator authority. Terminal combines engine activity with backend/frontend/network sources, stable poll failures, severity filters and redacted JSON download. Capture icon is immediately beside Clear. Server capture is bounded recent history; this is not a persistent full tracing system. |

### Mathematics checked against the requested example

For entry ₹1,373, 30 units, LTP ₹1,415.20, SL ₹1,174.05,
TSL ₹1,331.25 and target ₹1,770.90:

- Premium outlay: ₹41,190.00.
- Move: +42.20 points / +3.07%; gross MTM estimate: +₹1,266.00 / 0.21R.
- Defined SL loss: −₹5,968.50; trail downside from entry: −₹1,252.50.
- Target reward: +₹11,937.00 / 2.00R.
- ₹60 is a per-lot sizing allowance, not an actual broker tax/fee calculation.

These values describe a plan. They do not assert that the broker holds 30 units,
that its stop is active, or that it can fill the displayed price without slippage.

### Audit of retained profitability artifacts

`backend/study/kite_st_exit_analysis.md`, `kite_st_exit_sweep_results.csv`,
`kite_st_exit_mode_sweep_results.csv` and `kite_st_phase0_report.md` establish the
repository's own research limitations. The documented costed ATM/OTM options lens
had no OOS-positive configuration in its 60-cell comparison. Exit sweep rankings
had negative IS-to-OOS rank correlation; selecting the best cell would overfit.
The fast trail / one-red defaults remain unchanged. Fixed targets were not added
to a tail-dependent trend strategy. Deep-ITM/futures findings use modeled premiums,
modest profit factors, and do not clear the stated deflated-Sharpe standard.
They are a hypothesis for fresh validation, not a reason to silently increase leverage.
No new profitability figures were generated without authenticated market data.

Gamma's reference is signal-first: closed 5m E2 plus live-tick E1, OI and four
gates. `docs/strategy/gamma-move/README.md` remains the live reference. The new
shared replay exposes recorded/minute-proxy evidence; it does not assert tick
parity. Its D68 swing stop still cannot be represented as a broker premium trigger
before T1, so unsupported live swing entry remains blocked.

The older 5 October release audit is historical. Current Snapback code includes
owned plan production/arming/dispatch/protection; that earlier “not connected”
statement is not a current root cause. Current live-readiness evidence is used below.

### Functional and release evidence

Browser acceptance used the real running app with fixture GET responses for three
strategy boards and a read-only local backend. All non-GET fixture requests were
intercepted; only chart-state persistence was requested, and no broker order was sent.
Seven checks passed: three board/chart paths, identical ordered headers, chart replacing
open board detail, redacted valid JSON diagnostics with network events, and Help navigation.
No browser page errors. Phone Help viewport: 390px, document width 390px (no overflow).
The initial development dependency-cache errors disappeared after restarting the stale
Vite process; no production overlay dependency was added.

Live local HTTP checks verified health, account/readiness, and backend capture: 20/20
recent structured HTTP rows had correlation IDs. Readiness correctly refuses entry
because this test deployment intentionally sets `STERLING_LIVE_EXECUTION=0`, has no
active Kite account, lacks broker/egress acceptance, and was checked on a closed Sunday.
Snapback also reports its switch off, dispatcher not running and recovery required.
`lane_registry` is live-enabled by product default; the local kill-switch setting is
not a new permanent code block. No risk or recovery verdict was changed to PASS.

Validation totals and local evidence paths are recorded at final completion below.
See [the operator handoff](../ops/STERLING_OPERATOR_HANDOFF.md) for live preparation,
day-to-day use, recovery, and the evidence required before relying on trading returns.

### Final validation — 11 October 2026

| Check | Result |
| --- | --- |
| Full backend suite | 10,482 passed, 30 skipped |
| Final diagnostics/redaction regressions after full suite | 26 passed |
| Clock/replay/diagnostics focused follow-up | 24 passed |
| Full frontend suite | 1,888 passed, zero failures |
| Affected frontend regressions | 75 passed |
| TypeScript and production build | Passed |
| Kitelake onboarding suite | 294 passed |
| Chromium workflow acceptance | Seven checks passed; zero page errors |
| Python compilation, dependency consistency, diff whitespace | Passed |

Evidence is saved outside the checkout in
`/workspace/.sterling-cloud/validation/final-summary.json`, alongside the backend
JUnit report, frontend JSON report, browser report/screenshots and a SHA-256
manifest of changed artifacts. The full backend run preceded the final small
Bearer-token redaction correction; its relevant 26-test suite passed afterward.
Skipped tests are not claimed as verified. The build still reports a large main
JavaScript chunk (about 1.33 MB minified); bundle splitting remains a performance
follow-up. Browser acceptance uses controlled fixtures, not authenticated exchange
data. Broker receipts, protection placement, static egress and funded-account
acceptance remain unverified. No real order was submitted, and profitability
remains unproven.

### Follow-up: actionable entry refusal descriptions

Root cause: `kite_engine/service.check_live_readiness` labels the imported
`LIVE_EXECUTION_ENABLED` operator setting as a permanent release/code floor.
Gamma runner's `live_execution_disabled` reason and the board's older-payload
fallback repeat that inaccurate description. These messages obscure the actual
next action when `STERLING_LIVE_EXECUTION=0` deliberately halts new entries.
Update only descriptions to identify the operator master switch; retain existing
response keys/reason codes, import-time authority, all entry gates and exit paths.
Regression must verify the refusal remains blocked and identifies the switch.

Implemented the follow-up in SuperTrend readiness, Gamma runner refusal and Gamma
board fallback. The stable `live_execution_floor` response key and
`live_execution_disabled` code remain compatible. Validation: 43 backend
readiness/Gamma snapshot/live-audit checks and 33 rendered Gamma board checks
passed; diff whitespace passed. Tests assert both continued refusal and the
operator-switch explanation. No switch value or broker state was changed.

### Follow-up: replay ownership at every API boundary

Exact root cause: the process uses one `simulation_runner`. Board snapshots were
scoped, but simulation status/control/SSE routes did not enforce its `_uid` owner;
`force=true` could replace another user's session. Gamma scan/arm/adopt used
`has_session_view` globally, blocking a different user's live board. Two archived
strategy snapshot routes also returned the shared view without checking identity.

Contract before implementation: authenticated status for a non-owner returns an
empty IDLE view; controls and streaming refuse another owner's session; force
restart cannot steal an active or retained session. One replay slot remains shared
per process, with an explicit busy refusal until its owner clears it. Starting
and ownership assignment are serialized. SSE rechecks ownership before emitting
each queued event. Gamma replay guards apply only to the requesting owner. Apply
the same owner check to archived snapshots. Add a scoped replay refusal before
Snapback plan publication and arming, so historical controls cannot request live
execution through a direct API call. Exits and protection remain available.
Test owner/non-owner status, every control, force, streams and scoped entry refusals
with isolated runner/service doubles; do not send a broker order.

Impact tracing also found the older Adaptive Edge and Navigator API surfaces
using an unscoped replay view. Scope their view to the caller as well. The
retired Intraday runner returned before position maintenance whenever ANY replay
existed, contradicting its continuing-exits contract. Replace that global return
with a per-user entry/scan refusal AFTER reconciliation and rule/session exits;
replay must not halt management of existing exposure.

Client impact: `useReplayTransport.start` retried every HTTP 409/400 by calling
Stop then Start, even for another owner's busy refusal or invalid input. Limit
that retry to the explicit `already_running` code; show other server reasons
without attempting to stop a foreign session. Test both error outcomes through
the rendered hook and authenticated transport double. Existing replay API
fixtures must assign the authenticated caller as owner before seeding its ledger.

Implemented ownership across simulation status/control/SSE, the scoped Gamma
entry actions, Snapback publish/arm and legacy replay views. A lock serializes
start/owner assignment; foreign status is empty, force cannot steal a retained
session, and streams stop before another owner's queued event is emitted.
The operator can Resume, Stop, Clear and then release the slot for another user.
Intraday rule/session exits and reconciliation continue during replay. Client
retries only the explicit `already_running` code.

Focused evidence: all API tests passed (317 passed, eight skipped); owner-isolation
and Intraday focused run passed (79 passed, 12 skipped, before the extra
replay-active session-close parameter); four frontend ownership transport tests
passed. Production build and compile/whitespace checks passed. Earlier broad
validation found three replay API fixtures without owners; they now assign the
authenticated default operator before seeding results, preserving ledger/delta
assertions. An interrupted superseded full run is not passing evidence.

Local refreshed-runtime smoke verified health, empty idle replay status and the
actual operator-switch readiness explanation. No Kite accounts were configured.
Repeated Chromium acceptance passed all seven workflow checks with no page errors
and a 390px phone viewport without overflow. Frontend full suite: 1,892 passed,
zero failures. Replay remains process-local, one slot per process; this deployment
uses one API worker. No distributed replay coordination or restart persistence
is claimed.

### Final validation after ownership follow-up

Full backend: **10,506 passed, 30 skipped** in 397.94s. Full frontend:
**1,892 passed, zero failures**. Production build passed. Seven repeated
Chromium workflow checks passed with zero page errors; phone width remained
390px without overflow. Python compilation and diff whitespace passed.
The earlier interrupted run is superseded by this completed clean run. Existing
deprecation warnings and the large frontend main chunk remain follow-ups.

Latest reports are `backend-owner-final.xml`, `frontend-owner-final.json`,
`replay-owner-local-smoke.json` and `final-summary.json` under
`/workspace/.sterling-cloud/validation`. The refreshed local backend has zero
Kite accounts and its new-entry master switch off; no broker order or stop
placement was sent. These software checks do not establish profitability or
funded-account production acceptance.

### Local operator follow-up — 12 October 2026

The Linux checkout's authenticated local verification, additional board/position/execution/replay corrections, deployed source, test outcomes and exact broker limits are recorded in [the local strategy verification ledger](../ops/LOCAL_STRATEGY_VERIFICATION_2026-10-11.md). This local evidence supersedes the credential-free cloud instance's readiness observations, while keeping its historical validation record intact. New entries remain off by operator choice; both manual and ALGO controls remain available. No funded order or profitability certification is implied.

Local complete regression closure: **10,510 backend tests passed, 30 skipped, zero failures**; **1,921 frontend tests passed**, production build passed. The local verification ledger records the current source and results; its latest completed backend run supersedes the previously recorded local timing-failure gap. Broker-funded acceptance and profitability remain unverified.

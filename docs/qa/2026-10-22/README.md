# Sterling trading-session QA — 22 October 2026

> Follow-up merge/sync verification: [23 October report](../2026-10-23/SYNC_AND_FOLLOWUP.md) adds image/publisher identity, partial-fill risk, read recovery, chart route, test database safety and missing-target R fixes.

> Remediation update: application fixes are now implemented locally. See [FIX_IMPLEMENTATION.md](FIX_IMPLEMENTATION.md) and [VALIDATION.md](VALIDATION.md) for current status; the audit below is the preserved pre-fix baseline.

**Decision: NO-GO for an unrestricted live launch.** Funds and outbound-IP mismatch are excluded from this verdict, as requested. The remaining blockers are entry correctness, protection/UI contracts, and an unverified running backend version. A green build and passing lifecycle tests do not resolve these defects.

This is a trader-workflow audit of the four release strategies: **SuperTrend, Snapback, Gamma Unwind and Price Rules**. EMA Trend also appears in the app and was checked as an additional paper/research surface. It is not a fifth approved live originator. Five holding modes, manual versus automatic execution, simulation, replay, order submission, protection, exits, reconciliation, history and exports are distinguished below.

## What actually ran

| Evidence | Result | Meaning |
|---|---:|---|
| Backend admission, virtual/live-shaped E2E and journal suites | 222 passed | Production paths driven by a fake Kite HTTP transport; no real broker writes |
| Backend manual entry, stops, fired children, emergency exits, modes, alerts | 296 passed | Lifecycle and protection regression coverage |
| Backend Gamma contracts, Snapback execution, horizon/risk/canonical authority | 251 passed | Contract and risk regression coverage |
| Backend simulation/replay, EMA API and recovery | 723 passed, 5 skipped | Virtual boundaries and recovery; skipped tests are listed in the evidence summary |
| New backend defect characterizations + AMO wire capture | 13 passed | **Reproduces defects**, not 13 release approvals |
| Existing frontend suite | 2,222 passed, 18 failed | 238 files; failures in two test files, caused by an incomplete hook mock |
| New frontend defect characterizations | 2 passed | Reproduces Escape exits and invented copied SL using mocked APIs |
| TypeScript check, including new tests | Passed | `tsc --noEmit` |
| Production frontend build | Passed | Built to a fresh temporary directory; no deployment |
| Actual desktop browser | Inspected | Dashboard, PCR, all primary strategy entry points, settings, expanded Gamma plan, manual AMO ticket, notifications, actual Orders/GTT lists and Trade Journal |
| Actual Price Rules CSV export | Passed, header-only | Downloaded and parsed; no existing rule history to validate populated rows |

**Total backend: 1,505 passed, 5 skipped. Total frontend: 2,224 passed, 18 failed across the two runs.** These are targeted backend suites, not the entire repository suite. The complete existing frontend unit suite was run. No claim is made that browser Playwright E2E, every device, every live signal, or a real exchange session passed. Final actual screens showed zero open positions, no orders today, no GTT triggers, and no journal trades in the selected 30-day period; these do not independently certify all-time broker flatness.

Read [FINDINGS_AND_FIXES.md](FINDINGS_AND_FIXES.md) for exact source locations and acceptance criteria, [SESSION_RUNBOOK.md](SESSION_RUNBOOK.md) for the operator workflow, and [test-matrix.csv](test-matrix.csv) for explicit coverage and limits. The [issue register](issue-register.csv) lists all 18 open findings and scope/investigation items.

## Tested version and running-version limitation

- Checkout and published frontend: `ff137155a083b2c52981545ff94400764ad6c188`.
- Running backend process environment reports `STERLING_RUNTIME_SHA=9e989dd910995deb5e77385b983e5992c58883c0`, `ENVIRONMENT=production`.
- The running backend's working directory is this repository's `backend/`. Its reported SHA cannot be resolved in the local Git object database. Imported Python modules are not proven to match the current checkout.
- Consequently, source reproductions certify **the checkout's behavior**, not that a particular unsafe broker write occurred in the running app. The frontend identity does match the checkout.
- Local app inspected at `http://127.0.0.1:5180`. The separate 5174 development origin required login and was not authenticated during this audit. It shares the real backend and was not treated as a sandbox.

See [release-identity.json](evidence/release-identity.json). The initial clean checkout was on `dev`; `main` resolved to the same SHA. Only QA documents and characterization tests were added. A temporary Simulation visibility preference was restored to its original value. No application fixes, account changes, live arming, deployment, backend restart or real order submission were performed.

## Trading modes: what is and is not supported

| Strategy | Ultra scalp | Scalp | Intraday | Overnight | Swing |
|---|---|---|---|---|---|
| SuperTrend | 1m; freshness defect | 1/3/5m; freshness defect | 5/15/30m; freshness defect | 30m affected; 60m passes this check | 60m passes this check |
| Snapback | Minute plan/lifecycle exists | Minute plan/lifecycle exists | Completed-bar plan/lifecycle exists | Frozen carry policy exists | Daily setup / next-session entry / hedge lifecycle exists |
| Gamma Unwind | Not a production mode | Not a production mode | Supported, broker/both stop required | Not a production mode | Reference/research only; live entry refused |
| Price Rules | No five-mode engine | No five-mode engine | Explicit one-shot price conditions and owned exits | No separate carry-horizon selector | No separate swing-horizon selector |

SuperTrend's default intervals are 1/5/15/60/60m. Snapback's defaults are 1/1/15/60m for ultra/scalp/intraday/overnight, with daily swing scanning. Do not assume identical defaults because the labels match. Price Rules has a **trigger validity duration**, which is not a position holding deadline.

Unsupported combinations are not test passes. Gamma's correct refusal of live swing is an intentional protection constraint, not a bug to bypass. The runtime defaults and actual per-user settings still require verification on the selected release.

The strategy selector currently offers MANUAL and ALGO and intentionally filters out choosing SIMULATION unless a strategy is already in that state. The separate `SpectrumNavigation` does support an owner-authorized Simulation destination, controlled by saved workspace visibility. In this session Show Simulation was off. It was temporarily revealed for an access check, but cycling still skipped it under the current availability state; the original hidden preference was restored and verified. No complete virtual browser journey is claimed. The old `ViewSwitch` being unmounted is not proof that the app lacks a Simulation route. Simulation is covered through isolated backend tests. Historical replay is a separate feature and is not proof of broker execution.

## Signal → entry → protection → exit → artifacts

| Stage | Expected trader contract | Observed result |
|---|---|---|
| Start/login | Exact operator/account, valid Kite session, explicit market state | Existing account connected; raw unauthenticated API reads correctly returned 401 |
| Data | Source, instrument, exchange, candle interval, closed-bar time and age are explicit | Browser receives successful status/quote/scanner reads; Gamma reported incomplete historical recovery for NHPC |
| Signal | Entry-eligible is distinct from watching, historical or invalidated | Gamma WATCH buttons disabled correctly; historical Snapback disabled correctly; SuperTrend shows multiple mode histories |
| Manual preview | Exact contract/lot, entry price, actual plan SL/TSL/target, ownership and exit policy | Actual ICICIBANK AMO ticket opened, 700 units/one lot, explicit checkbox, plan stop shown; no Buy submitted |
| Request | One intended order, exact mode/account/signal revision, no silent next-day conversion | AMO serializer and cancel route correct in intercepted transport; stale SuperTrend signal can silently become AMO |
| Acknowledgement | Accepted is not filled and is not protected | Backend pending lifecycle tested; frontend wording and generic GTT watcher disagree with canonical engine ownership |
| Protection | Only confirmed owned quantity; verified broker stop; child state resolved before competing exit | Core tests passed, including gap-through-stop, partial fill and fired-child cases |
| SL / TSL / target | Price space and authority explicit; no invented stops or R values | Gamma no-stop reference plan displayed an invented calculator SL and 2R; Copy Plan also invents a stop |
| Exit | Exact owned remainder; no short/double sell; pending until broker confirms | Core tests passed; Price Rules Escape unexpectedly sends every owned full exit |
| Restart / disconnect | Reconcile before new risk; retain management; no ambiguous resend | E2E and recovery suites passed with mocks; running release mismatch remains unresolved |
| Journal / export | Signal, intent, order, fills, protection, exit and P&L join by stable IDs | Three strategy journal E2E tests passed; Price Rules is missing from the global journal (QA-18); its actual CSV header validated; populated live exports not available |
| End of session | Flatness or approved carry verified at broker; open orders/GTTs reconciled | Covered in automated lifecycle cases; actual market-close session not observed because market was already closed |

### AMO evidence and limits

The real `KiteClient` serializer was exercised with `httpx.MockTransport` and socket access blocked. It emitted:

```text
POST /orders/amo
exchange=NFO, tradingsymbol=NIFTY26OCT25000CE, transaction_type=BUY
quantity=75, product=NRML, order_type=LIMIT, price=100.05, validity=DAY
DELETE /orders/amo/QA-AMO-1
```

Symbols, quantities, credentials and replies in this capture are synthetic test fixtures, not current instrument recommendations. The durable manual-entry tests separately proved that both requested `regular` and requested `amo` can reach the mocked submission after a stale-signal refusal. Remaining account/capital/safety gates were satisfied by test fixtures to isolate that defect.

No real AMO was sent. Broker acceptance, rejection timing and exchange execution are therefore **unverified**, not passes. A real AMO is a queued financial instruction; a negative balance does not prove cancellation. Successful order submission also does not prove a fill, as stated in the [Kite order API contract](https://kite.trade/docs/connect/v3/orders/). GTT triggers and the orders they generate must be tracked separately under the [GTT API contract](https://kite.trade/docs/connect/v3/gtt/).

## Trader-facing review

**Beginner:** The strategy introductions, explicit disabled reasons, exact plan stop on the AMO ticket and separate requested/filled quantities are useful. The first landing surface was TV, and the saved trading layout gave Signals a very narrow dock. A new trader can see “Open”, “25 live”, “ALGO” and “LIVE” while there are zero owned positions and automatic entry is off. Those terms need distinct labels: **active setups**, **confirmed positions**, **automatic entry OFF**, **real account**, and **market closed**.

**Intermediate:** A signal must show its own mode/timeframe/source and invalidate in a consistent way. It should state whether stop/target values are in option premium or underlying points and whether a target is partial, final, modelled or absent. “Stop planned” must not look like “broker stop confirmed.” The shared calculator currently violates this distinction for Gamma.

**Expert:** The canonical intent journal, bounded quantities, partial-fill protection, no resend after unknown response, fired-child checks and per-position frozen mode policies are strengths demonstrated by tests. The gaps are release provenance, post-close signal provenance, a single protection owner, accurate acknowledgement states and reproducible mode-specific admission. Improving these is more valuable than adding indicators during a launch window.

The maximized board was readable; the initial narrow right dock clipped strategy names and trade columns. Light-theme secondary text and 9.5–11px footer/table labels were difficult to scan at the captured desktop size. Browser control frequently timed out, particularly on the 6,000+ element SuperTrend tree; some actions completed despite a reported timeout. This is an **inspection/performance concern**, not a measured app latency benchmark or a proven browser-specific production defect.

## Strategy evidence review — not a new backtest

Existing repository reports were read, not regenerated during this QA run:

- `docs/operations/supertrend_mode_sweep_2026-10-22.json`: all five modes have `accepted=false`. Core out-of-sample mean bps are **−2.96, −1.47, −4.82, −13.79 and −22.41** for ultra/scalp/intraday/overnight/swing. These report metrics must not be presented as actual option-account returns.
- `docs/operations/snapback_timeframes_backtest_2026-10-22.json`: ultra and scalp each record 190 trades across eight contracts, with only two profitable contracts. Intraday and overnight record zero trades in this sample. Daily swing uses modelled premiums. The sample does not validate every live lane or hedge economics.
- `docs/operations/gamma_modes_backtest_2026-10-21.json`: only one underlying signal was replayed across scenario labels. This is not independent evidence for six production modes; unsupported live modes remain unsupported.
- Price Rules is an execution tool whose results depend on the operator's rules. There is no basis here for a profitability claim.

Functional QA and evidence of a trading edge need separate decisions. None of the above is grounds to change the strategy parameters or increase position size during this audit.

## Launch decision and a two-hour repair sequence

| Window | Concrete output required |
|---|---|
| 0–20 min | Choose one backend/frontend release; identify loaded runtime; preserve state and reconcile exposure before any operator-managed restart. Keep release evidence truthful. |
| 20–55 min | Fix QA-02/03 freshness and AMO admission. Replace characterization assertions with desired-behavior regressions and prove rejected stale/unclosed/mismatched rows produce **zero** broker POSTs. |
| 55–85 min | Remove Escape liquidation, fake SL/R/copy values and client-side duplicate protection ownership. Fix acknowledgement wording and AMO status metadata. |
| 85–105 min | Repair the two frontend mocks; remove or label static market events; make maximized navigation reveal its destination; reconcile Simulation scope/documentation. |
| 105–120 min | Rerun affected gates on the selected release, record exact SHAs, inspect a complete intercepted entry/fill/stop/exit/journal chain for each supported strategy and mode. |

These are planning windows, not a promise that unresolved lifecycle changes can be safely released in two hours. If the gates remain unresolved, the honest outcome is a scoped research/manual-preview release, not certification of unrestricted live trading.

Required sign-off: no unresolved P0/P1 entry/protection defects; matching auditable runtime identity; all required tests green or individually adjudicated; supported-mode matrix accurate; pending acknowledgements never presented as fills/protection; populated journal/export joins verified; broker acceptance and actual market-session behavior explicitly identified as outstanding until independently observed.

## Evidence index

- [Machine-readable test results](evidence/test-results.json), JUnit XML and logs in `evidence/`.
- [Defect reproductions and AMO wire trace](evidence/defect-reproductions.log).
- [Frontend suite failure details](evidence/frontend-tests.log) and [new reproductions](evidence/frontend-reproductions.log).
- [Build/typecheck results](evidence/build-results.json).
- [Actual exported CSV](evidence/price-rules-export.csv).
- Screenshots: [initial dashboard](evidence/01-dashboard.jpg), [main dashboard](evidence/02-dashboard-main.jpg), [Gamma reference plan](evidence/07-gamma-plan.jpg), [Snapback](evidence/09-snapback-loaded.jpg), [Price Rules](evidence/12-price-rules.jpg), [manual AMO ticket](evidence/15-manual-amo-loaded.jpg), [trading configuration](evidence/16-trading-config.jpg).

Screenshots and AX captures contain local account/session information. They are local QA evidence; review before publishing or attaching to a public issue. No full credentials were copied into the report.

The repository asks for code-review-graph tools before exploration. Those tools were not exposed in this session; discovery returned no matching graph methods, so the documented fallback of targeted file inspection was used.

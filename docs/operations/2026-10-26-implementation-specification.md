# Sterling: Production Trading, Profitability Evidence, and Family Handoff
## Complete implementation specification and execution plan — v1.0

**Prepared:** 2026-10-26 (IST)
**Repository:** `nageshmadaram/sterling` (private)
**Observed remote baseline:** `main = dev = c2df68ebbb255a373b2f013c7d458a60862057df`
**Document classification:** Design proposal + evidence-indexed implementation backlog; not a release approval, financial forecast, or statement that the described work has been completed.
**Current authorization posture:** Do not enable new live entries, disable durable safety gates, place real orders, modify credentials, or imply profitable returns absent separately verified evidence and explicit broker/account-owner authorization.

---

## 0. Executive decision record

**Mission:** Deliver a highly available, operator-usable, secure Sterling platform with (A) technically certified broker lifecycle management and (B) separately validated, positive after-cost trading strategies, preserving an independent ability for authorized family members to operate accounts safely.

**Not a promise:** No codebase, optimization or deployment can guarantee a positive future P&L. A functioning execution engine and a profitable strategy are independent deliverables.

**Three explicit deliverables:**

- **R1 — Operational platform:** HTTPS frontend, production backend, stable persistence, broker-authenticated read-only data, observability, Telegram and mobile; new entries remain OFF.
- **R2 — Trading-certified:** Complete end-to-end order, fill, stop/protection, position, exit, restart, idempotency and disaster-recovery evidence on one immutable release identity; every interlock passes, human account-owner authorization obtained.
- **R3 — Economically validated:** A particular strategy/mode/instrument universe demonstrates robust positive expectancy after realistic expenses across non-overlapping, out-of-sample and forward windows, is monitored live at restricted risk, and can be suspended if its edge decays. No guarantee of monthly income.

**Hard rule:** `R1 != R2 != R3`. Failing or unknown evidence is not promoted by a UI switch, a code commit, a successful simulator, or a verbal declaration.

## 1. Evidence baseline and unknowns

### 1.1 Verified by connected GitHub on 2026-10-26

- Private GitHub repository exists and can be read; `main` and `dev` both point to `c2df68eb...` at time of verification.
- Last reviewed commit concerned history-read prioritization/preview isolation.
- Latest available GitHub Actions runs were marked failure, with `runner_id=0` and zero job steps in the inspected jobs. **This is not evidence that tests actually executed.** The cause must be verified from current GitHub account/job annotations rather than assumed to be a source failure.
- FastAPI and React are primary application layers; real orders use Kite; SQLite-backed application state, a canonical idempotent journal, release tooling, Telegram alerts and Cloudflare/ngrok gateway paths exist in the tracked source.
- `docs/operations/ISSUE_REGISTER_151_20261018.csv` contains 151 historical `OPEN_UNVERIFIED` acceptance states; it is not a current-runtime audit.
- Prior release evidence explicitly reports incomplete full restore, broker-IP/identity and live trade lifecycle proof.

### 1.2 Economic evidence (read from source reports; **historical, not live results**)

| Lane | Evidence | Interpretation |
|---|---|---|
| SuperTrend | `docs/operations/supertrend_mode_sweep_2026-10-22.json`: five tested modes have `accepted=false`; chosen OOS aggregate returns all negative | No evaluated mode can be promoted under the recorded gate |
| EMA Trend | `docs/operations/ema_modes_backtest_2026-10-22_fee3bps.json`: overnight 25 trades net -INR 15,430.90; intraday 1 trade net -INR 1,545.40; other tested modes zero trades | No defensible positive after-cost conclusion |
| Gamma Unwind | `docs/operations/gamma_modes_backtest_2026-10-21.json`: one signal across 15 sessions, zero sessions classified as complete | No statistically credible general performance estimate |
| Snapback | `docs/operations/snapback_timeframes_backtest_2026-10-22.json`: short-mode subsets report profits in two of eight tested contracts; other contracts have no trades or calculation errors | Candidate for research only; limited breadth and incomplete data invalidate a launch claim |
| Historical broad stock sweep | `BACKTEST_INDIAN_REPORT.md` shows selected historical winners but weak aggregate outcomes, e.g. mean SuperTrend net -1.1% across 198 configurations | Best-run selection is insufficient; selection bias and execution assumptions must be addressed |

### 1.3 UNKNOWN until verified on the actual host/broker

- Running backend SHA, frontend published SHA, actual `SAFE_MODE`, master live-execution flag, open exposure, pending intents, current Kite account binding and broker session.
- Stable public inbound hostname, approved outgoing static IPv4, registered OAuth callback, actual public access, Telegram delivery receipts.
- Present root disk headroom, memory budget, hosting allocation availability, complete backup restorability, and current end-to-end tests.
- Realized and unrealized production P&L, net after expenses, by strategy and account.
- Approved ability of family operators to use their own Kite accounts and app configuration; no credential sharing assumption.

## 2. Scope and prioritization

**P0 — must finish before *any* new live order:** broker identity, API whitelist, durable storage, margin/risk caps, kill switch, fresh exposure, journal, protection, reconciliation, sealed release, verified restore, alerts, emergency handoff, and independent operational sign-off.

**P1 — required to call the system operational:** backend/API availability, historical and live data quality, signal/plan visibility, deterministic mode labels, response times, account/position panels, HTTPS, mobile navigation, and durable session/UI preferences.

**P2 — required for the *money-making* objective:** research dataset, causal replay, cost model, walk-forward OOS, statistical robustness, paired broker execution evidence, strategy-specific economic gate, realized net P&L and live monitoring.

**P3 — separate experience workstream, non-blocking for trading certification:** IPTV/Radio, Spectrum toggles, media artwork, Shazam-like recognition, streaming UX and profile cropper. Fix independent critical security defects, but do not let media rewrites block or modify broker service safety paths.

**In-scope lanes:** SuperTrend, Snapback, Gamma Unwind, and Price Rules (individually armed/manual), each subject to release-scope authority. EMA Trend exists as experimental/research unless separately approved in code and economics; do not bypass `release_scope.py` to enable it. Archived engines remain isolated and manage existing owned positions only where required.

**Explicit exclusions:** guaranteed return amount/day/month, hidden automated placing of real orders, bypassing regulatory/broker controls, test-data fabrication, treating paper fills as broker fills, unrestricted family-account impersonation, and enabling every strategy simply because it is present in the UI.

## 3. Target logical architecture

```text
Operator web/mobile (HTTPS, RBAC, MFA where available)
                  |
              TLS ingress
                  |
         Authenticated API gateway
                  |
              FastAPI API
       +----------+----------+
       |          |          |
   Market data   Scanners   Operator controls
   adapters      /features  /readiness
       |          |          |
       +------> Strategy plans (immutable plan ID)
                         |
                 Economic lane gate
                         |
                Canonical risk service
                         |
                 Durable intent journal
                         |
                Broker execution worker
                         |
          Zerodha Kite API + order updates
                         |
          Fills / GTT or supported protection
                         |
            Position/exit reconciliation
                         |
   Append-only evidence + P&L + Telegram outbox
                         |
     Monitoring + snapshots + tested restoration
```

**Separation of planes:**
- **Research plane:** read-only market data, replay, parameter selection, OOS result files; no live broker order capability.
- **Observation plane:** live feed, order/position read-only reconciliation, dashboard and alerts; safe with entry toggle OFF.
- **Execution plane:** the sole path authorized to reserve, submit, protect and reconcile new risk. No alternate strategy module, UI endpoint, replay, Telegram command or background task may call the broker directly to increase exposure.
- **Safety plane:** master halt, safe mode, binding, funds/caps, freshness, protection state, event-loop health and disk/evidence durability; fail-closed for new risk, never intentionally block risk-reducing exits.

**Account isolation:** Every intent, order, fill, stop, report and alert has `account_id`, `deployment_id`, `strategy_id`, `mode`, `strategy_version`, `release_sha`, `signal_id`, UTC timestamp, and immutable `broker_order_id` when known. Production and simulated tenants may share code but never permissions, database namespace, broker tokens or attribution.

## 4. Functional requirements and contracts

| ID | Requirement | Acceptance evidence |
|---|---|---|
| FR-01 | Broker sign-in and user binding | Valid Kite login; account/holder identity read back; user-initiated logout; expired token blocks new entries |
| FR-02 | Data acquisition | Timestamped quotes/candles/OI where entitled; deduped, no lookahead, explicit stale/partial/missing states |
| FR-03 | Signal computation | Reproducible strategy version, instrument, mode, trigger, stop, target, expiry and dataset reference |
| FR-04 | Signal-to-plan | Every visible actionable row has canonical plan ID and exact eligibility/refusal reason; empty boards differentiated from disconnected scans |
| FR-05 | Pre-trade admission | Account identity/funds/exposure/risk/clock/disk/source/permissions checked twice, refusal machine-readable |
| FR-06 | Idempotency | Exactly one durable reservation for `(account, signal, strategy, side, plan_version)`; ambiguous response never automatically resent |
| FR-07 | Broker execution | Broker acknowledgment tracked independently of exchange fill; terminal and partial states reflected in ledger |
| FR-08 | Protection | Confirmed broker-held protection where product/contract supports it; stop may tighten but not loosen unintentionally; unknown protection => UNPROTECTED and repair/exit escalation |
| FR-09 | Exit management | Target/SL/TSL/manual emergency exit, partial fills, position size changes, stop movement and broker reconciliation work on restart |
| FR-10 | Risk and halts | Master switch, safe mode, global/account/strategy caps, intraday drawdown halt, max lots, exposure and correlated-risk ceilings |
| FR-11 | Audit ledger | Immutable events, no silent mutations, source identity, causality, recorded rejection and every lifecycle receipt |
| FR-12 | Operator alerts | Signal, reserved, submitted, partial/complete fill, protection, modification, closed, halt and recovery messages are durable, acknowledged and nonduplicated |
| FR-13 | Visibility | Orders/holdings/positions live broker truth clearly separate from simulated, restored, stale, historical or modeled records |
| FR-14 | Market-hours control | Exchange calendar, timezone, holiday, derivative expiry, session/cutoff rules and fresh data correctly gate entries |
| FR-15 | UI/mobile | Responsive account-aware strategy boards; search, filters, default Home, profile, settings persistence, mobile back/close and accessible emergency stop |
| FR-16 | Emergency recovery | After reboot/unknown order/DB issue, starts in RECOVERY_REQUIRED and settles broker truth before new entries |
| FR-17 | Multi-user continuity | Least-privilege operator accounts, documented credential rotation, account-owner authorization and portable encrypted backups |
| FR-18 | Economic truth | Per-trade realized P&L including fees/slippage, open P&L separately, tax estimates separate, strategy-level net expectancy and risk metrics |

## 5. Strategy specifications

### 5.1 Common mode semantics

Use an explicit schema; never infer `scalp/intra/ultra` by string heuristics. For each strategy support only modes explicitly implemented and validated for that lane: `ultra_scalping`, `scalping`, `intraday`, `overnight`, `swing`; auto mode is a selector and must not hide its chosen underlying mode. UI may display ineligible modes but must not arm them. Preserve existing rules unless a separately versioned economic hypothesis is approved.

Every mode carries timeframe, signal lookback, candle-close policy, minimum market depth, max holding period, entry cutoff, stop/exit policy and max cost budget. Different mode = separately backtested version and acceptance.

### 5.2 SuperTrend

- Freeze current Triple SuperTrend rules, forming-candle exclusion, alignment, exit trigger and dedupe, rather than changing parameters opportunistically.
- Repair and test any trailing-stop loosen-on-scale-in, market-hours edge, duplicate-order, staged fill and reconnect paths.
- Record first-stage scanner trace -> signal -> plan -> risk refusal/acceptance -> broker result -> stop -> exit.
- **Economic status:** five evaluated modes currently rejected in October 22 OOS sweep. No autonomous new positions until a revised, preregistered hypothesis passes all economic gates.

### 5.3 Snapback

- Maintain explicit hedge ownership for strategies that need an index-futures hedge; failure/waiver of required protection causes risk-reducing handling, not naked exposure.
- Fix historical options error paths (`object of type 'int' has no len()`), incomplete-day metadata, contract date-effective mapping and no lookahead.
- Ensure scan rows persist read-only after restart but cannot be actioned until fresh source and eligibility data arrive.
- Reevaluate reported short-mode profit on independent periods, independent contracts, realistic execution, and contemporaneous hedging prices; do not extrapolate a handful of profitable contracts to the entire universe.

### 5.4 Gamma Unwind

- Complete watchlist rebuilding after mid-day restart; distinguish genuinely untraded option contracts from infrastructure/provider failure.
- Avoid 17–66 minute obsolete scans by request budgeting, scoped incremental historical reads, bounded worker concurrency and reuse of immutable observations across tenants.
- Require per-day historical coverage completeness and date-effective option chains; the current 15-session test includes no fully complete session.
- Manual/offline evidence capture until reliable coverage and OOS performance. Never flip `auto_execute` to make the board appear active.

### 5.5 Price Rules

- Distinct ON/OFF state for each armed plan; global master control visible in What's Running; present difference between plan armed and global entries blocked.
- Once-only trigger IDs, price-source timestamp, entry cutoff, stop/target/TSL, max quantity and idempotent handling.
- Manual-plan performance reported separately from algorithmic alpha; no inference that a profitable discretionary rule validates other lanes.

### 5.6 EMA Trend (experimental)

- Universe selectors for NIFTY, BANKNIFTY, FINNIFTY, SENSEX and eligible F&O instruments; simple symbols and optional custom additions, resolved server-side to the correct contract metadata.
- Uniform strategy/mode dropdowns, instrument-type labels, auto-refresh and truth-based status (not synthetic filled signals).
- Diagnose zero-signal paths and data availability in read-only simulations; separately research per-mode rules with out-of-sample evidence.
- Keep off the production release originator allowlist until both software and economic gates pass.

## 6. Quantitative research and profit validation — critical workstream

### 6.1 Data provenance

Build a dataset catalog keyed by provider, timestamp, exchange, spot/future/option ID, date-effective instrument master, OHLCV, bid/ask/volume/OI where available, revision and checksum. Missing bars, holidays, expiries, corporate actions, reverse splits, late corrections, stale quotes and unavailable expired options must have explicit gap codes. Store both data coverage and strategy decisions; no clean rescan can erase a historical incomplete-session marker.

### 6.2 Simulation fidelity

Signal calculation must use only data known before entry. Model actual derivative lot sizes, bid/ask spread, order type, available liquidity, execution delay, slippage, partial fills, stop gap-through, brokerage, exchange and statutory charges and realistic short-sale/hedge margin. Prefer actual historical options quotes/prints; label modeled options pricing as `MODELLED` and **exclude** it from a `REAL_EXECUTION_VALIDATED` metric.

### 6.3 Pre-registered hypothesis and splits

For each candidate, record `hypothesis.md` and immutable versioned config before evaluating holdouts; date-ordered train/validation/test, purge and embargo for overlapping labels, walk-forward periods spanning market regimes, stress tests and cross-instrument confirmation. No repeated selection on the same final holdout. Track all attempted parameter combinations to expose multiple-testing/selection bias.

### 6.4 Promotion criteria (proposed gates, not claims about results)

An experiment must meet **all** before consideration for controlled forward evaluation:

1. Data integrity and session completeness confirmed; material gaps cannot be ignored.
2. Net OOS per-trade expectancy after all modeled costs **positive**; lower confidence bound should be positive at a preregistered confidence level, with dependence-aware bootstrap/session clustering.
3. Positive across multiple non-overlapping windows; no single symbol, regime or anomalous trade drives the aggregate.
4. Materially positive under predeclared sensitivity to fees, latency, stop slippage, liquidity and adverse regime.
5. Drawdown, expected shortfall, turnover and capital use within an independently approved risk budget. Never define acceptable drawdown after observing results.
6. Forward shadow results reconcile against actual observed quotes, without hindsight or paper-to-real conflation; exceptions and missed fills counted.
7. Before scaling, a sufficiently sized live observation sample across meaningful market regimes, with matched broker receipts; determine sample adequacy using variance/power rather than a fabricated fixed promise.
8. Regulatory, broker, technical and operator gates pass simultaneously.

**Reporting:** per strategy/mode/symbol/session: n signals, n refused, n accepted, n broker submitted, n partial and complete, hit-rate, payoff ratio, gross/net/fees, profit factor, expectancy, confidence interval, max drawdown, tail risk, exposure, time-in-market, capital utilization, data coverage and evidence type (`REAL_BROKER`, `REAL_MARKET_SIM`, `MODELLED`, `HISTORICAL`). Never combine incomparable categories in one profitability number.

**Fail policy:** strategy remains observation-only when results are negative or inconclusive. Continuous drift-monitoring can demote a formerly approved lane; trading is optional, preserving capital is mandatory.

## 7. Broker execution state machine

```text
SIGNAL
  -> PLAN_CREATED
  -> PRECHECK_APPROVED / REFUSED
  -> INTENT_RESERVED
  -> PRE_SUBMIT_CHECK
  -> SUBMITTING
  -> ACKNOWLEDGED / REJECTED / UNKNOWN
  -> OPEN / PART_FILLED / FILLED
  -> PROTECTION_PENDING
  -> PROTECTED / UNPROTECTED
  -> EXIT_REQUESTED
  -> EXIT_PART_FILLED / EXIT_FILLED / EXIT_UNKNOWN
  -> RECONCILED / RECOVERY_REQUIRED
```

**Invariants:** one authoritative broker writer; write-ahead journal before submission; recovery reads broker truth before any retry; `order_id` does not equal filled order (Zerodha documents this distinction); webhook/postback and authenticated read reconciliation may race, so transitions monotonic and version checked. Unknown replies never blindly resubmitted. Partial fills cause protection to match actual filled quantity. Rejected protection demands repair or risk-reducing exit, alerts and halt. Restart carries no new-entry permission until pending intents and external exposure are resolved.

**First-day controls:** define capital allocation from available, authorized funds; maximum one lot/entry; finite positive daily-loss and gross-exposure budgets, portfolio concentration caps, absolute max order notional and per-lane switches; stricter operator-approved limits supersede defaults. Ensure no default `true` for an absent live-entry flag and no split-brain workers.

## 8. Regulatory and broker interface constraints

- Authenticate with Kite via backend OAuth only; no browser/mobile embedded API secret or token logs.
- Confirm account-owner authority, applicable exchange broker policy and current Zerodha developer settings for each deployed Kite application.
- A **whitelisted static IP** is required for Kite API-based order placement under Zerodha's published policy; read-only data and orderbook APIs may work from non-whitelisted IPs. The host's observed IPv4 is not proof of console approval.
- Zerodha documents a **10 orders per second** limit (with other per-minute/day limits); design a lower configurable per-account safety budget and obey the provider's then-current documented limits. Registration conditions for higher-rate algorithms must be separately assessed; do not interpret this document as legal authorization.
- Broker acknowledgement is not a fill. Use postbacks plus order/trade and position endpoints, with reconciliation and provenance. Any missing, changed or unsupported stop/order variety must be blocked or explicitly handled.
- Parents should use their own authorized broker identities and approved app arrangement. Never copy passwords, OTPs, access tokens or identity bindings between users.

Official references (verify before deployment):
- https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip
- https://kite.trade/docs/connect/v3/orders/
- https://www.kite.trade/docs/connect/v3/exceptions/
- https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html

## 9. Production hosting and deployment specification

**Compute prerequisites (measure, don't invent):** host with stable outbound public IPv4 approved in Kite console; reachable stable HTTPS DNS name; persistent encrypted storage; secure SSH key; pinned runtime; sufficient CPU/RAM for worst observed Gamma data load; independent backups; update/rollback capability; time synchronization. Provision by benchmark, not provider's free-tier label.

**Choice:** OCI Always Free ARM A1 only if capacity is available and resource quotas plus architecture dependency tests support full deployment. Prior observed A1/AD-1 shortage is unresolved; do not assume acquisition. A tiny 1 GB x86 Always Free instance is not automatically suitable for this workload. Alternative is an appropriately sized, budget-approved VPS with a stable IPv4. Avoid using changing ngrok/quick-tunnel URL as production callback address.

**Topology:** reverse proxy terminates TLS (HSTS, secure cookies), API on loopback, allowlist outbound to broker/data, only 443 public (and restricted SSH), no public SQLite/Redis/debug ports, deploy one authoritative broker execution worker/account, optional separate analytics worker, watchdog and host alerting. Cloudflare can be the ingress only if the broker callback remains stable; outbound IP still independently controlled.

**Release discipline:** build immutable source SHA/tag and manifest of Python/frontend/dependencies/migrations/config hashes; no dev-only asset promotion. Never update live backend through frontend commit auto-publish. Migration backup first, dry-run, stop new entries, reconcile and drain, deploy, verify, reopen only after explicit evidence. Pin dependencies, scan and record SBOM.

**Durability:** preserve live `STERLING_DB_PATH`, WAL/journal and separate evidence storage; database and object backup encrypted, checksum verified, periodic off-host full restore into isolated environment. New host must not silently create an empty database in place of existing positions. Test disk-full, reboot, incomplete restore and unreachable object storage.

**SLO proposals (must benchmark):** healthy public login and order-monitoring availability during market sessions, bounded tick-to-signal and signal-to-admission latency, alert delivery observation, zero knowingly unprotected positions beyond the documented emergency repair interval, zero duplicates after ambiguous broker outcomes. Choose numeric latency/RTO/RPO thresholds from measured host profiles and account risk limits; do not claim them pre-measurement.

## 10. Security and permission design

- Least-privilege operator/observer/admin RBAC with explicit account scoping; require fresh authorization for any live-risk enablement.
- HTTP-only/SameSite/Secure cookies, CSRF defense where cookies are used, restricted CORS, websocket auth, rotating refresh/session and API credentials, secrets from protected environment/managed secret store.
- Redact PII, API keys, broker tokens, user IDs and execution payload secrets from GitHub, logs and Telegram.
- Enforce server-side authorization for profile, media, strategy toggles, order actions, settings, and all websocket subscriptions.
- Validate all inbound instruments/modes/quantities against authoritative metadata; rate limit expensive replay and scan endpoints.
- Scan dependencies and container images; prohibit unsafe script-based auto-actions and secrets in frontend build.
- Preserve tamper-evident source/strategy version/evidence linking; backups encrypted and access restricted.

## 11. UX / mobile / media boundaries

**Trading surfaces:** Kite home, signal boards, Orders, Holdings, Positions, P&L, readiness, alerts and emergency halt share a consistent state model, refusal reason codes and timestamps. Make broker/protection unknown states unmissable. No synthetic P&L shown as real account balance. Search and modes in one clear row; strategy controls only where operator role authorizes; no overlay blocks emergency stop or causes background click-through.

**Spectrum:** preserve configurable Kite, Simulation, Space (TV), Radio visibility/order/home logic, mobile-only close button on non-home pages, persistent selection across reload, functional hamburger open/close and overlay dismissal. Media sections must be isolated from live trading runtime and safely lazy-loaded.

**IPTV/Radio:** separate P3 backlog for touch gestures, fullscreen lifecycle, PiP, player opacity, channel thumbnails, search/settings equivalence, profile modal focus trapping and recognition of station metadata; do not claim song recognition where no reliable metadata/audio fingerprint permission exists. Media regressions cannot gate broker exit or recovery uptime.

**Family operator UX:** simplified default dashboard with four statuses: BROKER, RISK, PROTECTION, AUTOMATION; explicit no-trade/alert-only posture; emergency actions and readable handoff. Handoff works without Python, SSH, Git or direct DB edits.

## 12. Test and acceptance matrix

| Layer | Required tests | Evidence and pass criterion |
|---|---|---|
| Static | `python -m compileall`, shell `bash -n`, TypeScript `tsc --noEmit`, schema validation | Zero parse/type errors on candidate SHA |
| Unit | Strategical signal parity/frozen fixtures, options calendars, cost computations, risk admission, state machine | Deterministic, no skipped critical assertions |
| Integration | Kite adapter mock/recorded responses, DB transactions, websocket/postback races, Telegram provider double | No duplicate broker intents; monotonic state transitions |
| Contract | API schemas; access scope; UI adapter/board state; backend/frontend DTO parity | Failed/stale/blocked states unambiguous |
| E2E offline | order/partial/reject/timeout, stop/GTT, exit/restart, fallback, multi-account isolation | All safety invariants and rollback assertions pass |
| Security | AuthZ/CSRF/CORS/TLS, secret scanning, dependency audit, token expiration, replay/price manipulation, privilege separation | No critical/high unresolved issue without documented external authorization; no secret leak |
| Load | scans, WebSocket ticks, history catch-up, operator monitoring, event-loop stalls | Predeclared service SLO met in measured representative trading load |
| Resilience | 12 existing failure drills + disk-full, stale feed, broker outage, ambiguous acknowledgment, corruption and isolation | Fail closed on new exposure; risk-reducing path available; incident evidence durable |
| Recovery | Full backup + off-host restore, actual broker-state reconciliation, identical lane manifest hashes | Restored state valid; outstanding risk not forgotten |
| Human | Fresh account binding, Kite app observation, operator B1–B7, Telegram phone receipt | Independently signed factual acceptance; no inferred PASS |
| Financial | OOS, walk forward, multiple-testing and forward logs; live trade economics | Pre-registered per-lane promotion requirements met; otherwise observation-only |

**Suggested existing commands on the appropriate machine** (start in no-new-orders mode and verify environment first):

```bash
cd ~/Sterling
git status --short --branch
git rev-parse HEAD
scripts/sterlingctl status
scripts/sterlingctl doctor
scripts/sterlingctl exposure
scripts/sterlingctl bindings
scripts/sterlingctl drills
scripts/sterlingctl certify strict
bash scripts/release_certification_probe.sh
```

**Caution:** `sterlingctl` may modify diagnostic/operator state; run only after reviewing that instance's subcommand effects and while no unintended live orders are permitted. Do not restart the production worker merely to make diagnostics green.

## 13. Release/approval gates: evidence authority

| Gate | Pass condition | Accountable role | Failure disposition |
|---|---|---|---|
| G0 Source | Exact branch SHA, clean checkout, pinned deps, provenance | Engineering | Block deployment |
| G1 CI | Actual jobs executed on current SHA (not runner_id=0), complete suite + build + smoke pass | Engineering | Block promotion |
| G2 Host | Disk/RAM/performance and persistent storage meets SLO, durable backup tested | Operations | Block new entries |
| G3 Broker | Current approved outbound static IP, OAuth callback, correct account binding, fresh inventory | Account owner | Block new entries |
| G4 Safety | Master/safe/recovery switches, exposure/caps/locks, partial/unknown order tests, reliable stops | Engineering + risk owner | Block new entries |
| G5 Recovery | All relevant failure drills, physical restore, cross-session startup with safe reconciliation | Operations | Block new entries |
| G6 Alert | End-to-end Telegram operator-device delivery and outage/retry observed | Operator | Block unattended automation |
| G7 Economic | Strategy/version independently approved with complete after-cost OOS/forward evidence | Research/risk reviewer | Leave lane disabled |
| G8 Operator | Independent responsible person can detect/fix/stop anomalies and reach broker account | Account owner + delegate | No unattended production |
| G9 Limited live | Separately authorized controlled order + stop + exit evidence; real loss limits | Account owner | Stop expansion; rollback/reconcile |

These are **necessary** conditions, not a claim that a pass guarantees returns. Existing `doctor` output may treat some release controls as advisory; the certification register must preserve `UNKNOWN/BLOCKED` until independently observed evidence is recorded.

## 14. Prioritized implementation epics and dependencies

| Epic | Priority | Implement | Source targets | Definition of done |
|---|---|---|---|---|
| E00 Preserve & measure | P0 | Freeze read-only snapshots, full operator inventory, disk, active SHAs, broker read-only state; copy encrypted backups without exposing secrets | `scripts/sterlingctl`, `.runtime`, `docs/operations` | Timestamped evidence of source, host, broker, storage; new entries OFF |
| E01 CI runner | P0 | Diagnose 0-step jobs; resolve GitHub Actions account/runner eligibility using authorized account action; run local exact-commit fallback without reclassifying it as CI | `.github/workflows`, test runners | Actual tests run on current SHA; direct logs attached |
| E02 Runtime health | P0 | One supervisor/backend owner, recoverable server, no event loop sync I/O, bounded scans, stable gateway | `backend/app/core/async_work.py`, Gamma runner/scanner, stack scripts | Repeated moving-market load and restart drill meet measured SLO |
| E03 Host and identity | P0 | Static IPv4, permanent hostname, TLS, remote ingress, Kite callback/session, real account binding | `deploy/`, `docs/operations/BROKER_ACCOUNT_HANDOFF.md` | Independent broker console and network proof; no unapproved IP |
| E04 Safety authority | P0 | Cross-lane guard parity, admission double-check, caps, kill, protection, no alternate submit path, full lifecycle reconciliation | `backend/app/core/release_scope.py`, broker/execution and risk services | Order journal transitions and failure drills certified |
| E05 Restore & handoff | P0 | Full sealed deployment backup, lane manifests, encrypted off-host restore, account-owner access continuity | `docs/operations/`, backup scripts | Independent restore and operator drills pass |
| E06 Data quality | P1 | Date-effective symbol masters, option history provenance, gaps, OI, no-lookahead, missing session and fees | data lake/backtest adapters | Quality report with 100% attribution for included events; incomplete data excluded |
| E07 SuperTrend | P1/P2 | Frozen parity, no stop-loosen, mode-specific OOS, truthful states | SuperTrend engine and regression suites | Complete lifecycle tests; economic gate per mode |
| E08 Snapback | P1/P2 | Hedge admission, real option data, backtest errors, restored read-only rows, risk states | Snapback scanner/planner/wiring | No data errors or naked hedge waiver; OOS gate |
| E09 Gamma | P1/P2 | Restore watchlist after restart, bounded incremental scan, coverage, timeout, OOS | Gamma historical/scanner/runner | Fresh watchlists after restart, bounded latency; coverage and OOS gate |
| E10 Price Rules / EMA | P1/P2 | Per-plan toggles, metadata, modes/labels, execution path; EMA experimental by default | plan/EMA engine + frontend controls | Tests and authority; no broad unvalidated auto-enable |
| E11 Economic research | P2 critical | Holdout registry, stress/fees model, confidence estimates, forward recorder, trade attribution | `research/`, `backend/app` analytics | Reproducible economic report; no lookahead or leakage |
| E12 Observability | P1 | Readiness, real P&L provenance, telemetry, error budgets, alert receipts | status, evidence, Telegram, frontend | Alerts reach device; full audit event trace |
| E13 Product UX | P1 | Mobile overlay/home/prefs, signal display, accessible emergency halt, no stale/real confusion | `frontend/`, `mobile/` | Mobile/desktop acceptance matrix passes |
| E14 Media isolation | P3 | IPTV/Radio regressions on separate bounded workstream | `sterling-iptv/`, `sterling-radio/` | Core trading readiness unaffected |
| E15 Controlled launch | P0/P2 | Release freeze, strict certify, approved live limited session, monitor + rollback | `docs/operations/FINAL_RELEASE_RUNBOOK.md` | Actual receipts and explicit owner sign-off; no auto-scale |

## 15. Recommended execution order / critical path

```text
A. Snapshot source + host + broker (read-only)
       |
B. Fix CI/runner evidence + local test reproducibility
       |
C. Stabilize host storage/network/backend + restore
       |
D. Broker identity, egress and risk interlocks
       |
E. Complete lifecycle offline + observed read-only live feed
       |
F. Research data corrections -> frozen OOS + forward observations
       |
G. Seal exact source + deployment release (R1)
       |
H. Independently verify broker order lifecycle with owner approval (R2)
       |
I. Per-lane economic promotion, limited forward/live measured P&L (R3 candidate)
       |
J. Restricted scaling only after sufficient market/regime evidence
```

**Parallelization:** E06/E11 can run while E02/E03 are fixed but must not touch operational broker state. E13/E14 can run as isolated workstreams; do not merge UI changes during final release freeze. E04/E05 remain prerequisites for any increase in live risk.

**Sequencing strategy:** Fix a thin, certifiable end-to-end slice (one account, one explicitly allowed strategy, one instrument, one lot cap, read-only -> controlled authorization) before adding universe breadth. Do not activate all four engines at once.

## 16. Delivery stages and artifacts

**Stage A — Foundation:** Output `baseline.json` and `blocking-gates.md`; SHA, active branch, complete issue list, separate last-known/unknown evidence; verified backups; CI executed or marked BLOCKED with reason.

**Stage B — R1:** Versioned stable HTTPS deployment with read-only Kite, live market data, strategy boards, alerts, source/build identity and rollback. Demonstrate reconnect, zero unintended entries, full restore and minimal operator UI.

**Stage C — R2:** Exact release candidate tag, test logs, current broker-approved network identity, reconciled account, verified protection/exit receipts (with explicit approval), failure drill report and signed runbook handoff.

**Stage D — R3 research:** Immutable datasets, per-mode hypothesis register, train/validation/holdout, independent robustness/OOS and forward-monitoring reports; activate only verified eligible strategy, otherwise no live entry.

**Stage E — Family continuity:** Separate authorized account onboarding where permissible, guided operator runbooks, encrypted backup access and recovery stewardship, Telegram recipient tests and explicit financial risk disclosure.

## 17. Operator checklists

### Safe preflight (read-only intent)
- [ ] Record current SHA, clean/dirty status, local/remote divergence and ongoing orders before any merge/restart.
- [ ] Confirm live-entry flag and durable safe mode are OFF/ON respectively as intended; do not assume flags from docs.
- [ ] Read-only broker profile/positions/orders/GTT/funds successfully refreshed; UNKNOWN is never flat.
- [ ] Record actual static outward IP, whitelist evidence, current callback, token expiry, market feed and subscription.
- [ ] Verify disk capacity, systemd owner, logs, build source, API/WebSocket and Telegram route.
- [ ] Copy SQLite/evidence/config backup and restore independently; preserve all manifests and encryption material.
- [ ] Confirm all unresolved acceptance IDs are tracked and prioritized, not silently marked passed.

### No-go triggers
- Any unknown order or external broker exposure, unfresh funds, missing stop, unconfirmed ownership, broken egress, failed restore, evidence-store error, full disk, unknown time, insufficient margin, unreadable Kite or an already failed economic gate.
- Any mismatch between tested SHA and serving backend/strategy version, or failure of emergency human control.
- Any claimed profit originating exclusively from simulated/modelled instruments without clear label.

### After separately approved initial live order
- Watch in Kite independently: order acknowledged, actual filled quantity, correct open exposure, independently confirmed protective stop/GTT, exit filled, fees/net ledger correct, journal consistency after restart.
- At first discrepancy: disable **new entries**, retain protection/exit ability, reconcile, alert human operator, log an incident; never automatically double-submit unknown intents.

## 18. Rollback, failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| Static IP/callback changes | IP + developer console identity mismatch; authentication failure | Block new orders; preserve monitoring/positions; update only after independent broker approval |
| Duplicate backend/worker | Multiple owner PIDs, port collision, journal lease conflict | Single writer/lease with fencing and fail-closed admission |
| Unknown order acknowledgment | Timeout after POST, unresolved journal reservation | Query broker trades/orders; no automatic retry; operator escalation |
| Stop not accepted or partial fill | Broker order/GTT state differs from fill quantity | UNPROTECTED; repair/flatten under authorized runbook; halt new risk |
| Data gaps/stale OI | per-symbol missingness, observed timestamps/TTL | Reject new plan or mark session incomplete; no synthetic reconstruction passed as real |
| Model overfit | OOS decay, sensitivity, too many trials | Freeze holdout, adjust hypothesis only in new experiment, lane off |
| Operator absence | Unacknowledged alarm, unavailable operator | New automated risk disabled; broker-native protection stays; designate authorized backup operator |
| Disk full/DB corruption | disk thresholds, WAL checkpoint failure, integrity check | Halt new risk; off-host verified restoration and current broker truth reconciliation |
| Public tunnel outage | HTTPS probe fails | Backend stop/exit worker independent of ingress; stable domain recovery, no auto-restart of broker path |
| Rollback with open position | serving SHA changes, journal version mismatch | Freeze entries, reconcile/handle exposure first; migrations backward compatibility assessed |

## 19. Authority, evidence and implementation discipline

- **Engineering:** may change source and tests under reviewed branch; may not declare live broker proof from mocks.
- **Research:** may propose strategy changes with hypothesis + independent validation; cannot change live release originators without safety/risk approval.
- **Operator/account owner:** alone authorizes live broker risk, correct broker account, amounts and limits, static IP registration, emergency exits and credentials.
- **Family operators:** need their own legitimately authorized access; source availability alone does not transfer account ownership.
- **Evidence register:** append-only record `claim_id`, source_sha, runtime_release_sha, dataset_id, observed_at, source_type, test_command, log_hash, result and independent reviewer. Classification: `VERIFIED`, `FAILED`, `BLOCKED`, `UNKNOWN`; never equate `CODE_IMPLEMENTED` with `VERIFIED`.
- **Branch discipline:** only reviewed main/dev policy; do not force-push, delete, merge or overwrite unknown local/remote work. No blanket `git add -A`; never commit credentials, SQLite, journals, encrypted keys or private financial receipts.
- **Change management:** small atomic commits with relevant tests, code diff review, issue IDs, strict no-go check, then advance immutable candidate SHA. Each change resets only the evidence it actually invalidates.

## 20. Work immediately authorized by this plan vs work requiring external action

**Can begin in repo without real-money risk:** issue inventory, unit/contract testing, CI diagnosis, software fixes, research dataset validation, economic evidence reanalysis, UX regressions, source-level security review, deployment template and read-only diagnostics.

**Requires the actual host:** measure running processes and disk, inspect protected live logs, backup/live restore, compile/deploy/build, benchmark current strategy scan latency and network callbacks. The GitHub connector alone cannot perform these checks.

**Requires the authorized broker/account owner:** static IP whitelisting, app callback, fresh Kite login, account binding, risk budget, separately approved test orders, emergency liquidation, 2FA and family account delegation where allowed.

**Requires markets and time:** genuine live quote/option OI observations, trading-hours disconnect drills, stable forward-period performance and independently observed post-fill lifecycle.

**Current go/no-go conclusion:** `R1 = not yet independently certified`, `R2 = blocked`, `R3 = unsupported by available strategy evidence`. Proceed with source-level stabilization and data/research validation; do not turn on live orders or make family-income projections.

---

## Appendix A. Source map and baseline references

Repository-native artifacts inspected:
- `README.md`, `DEPLOYMENT.md`, `RISK_MANAGEMENT.md`, `docs/operator/START_HERE.md`
- `docs/operations/FINAL_RELEASE_RUNBOOK.md`
- `docs/operations/READINESS_EXECUTION_20261018.md`
- `docs/operations/LIVE_LIFECYCLE_CERTIFICATION_20261020.md`
- `docs/operations/PRODUCTION_FIX_PLAN_20261021.md`
- `docs/operations/2026-10-23-open-gaps-implementation-spec.md`
- `docs/operations/ISSUE_REGISTER_151_20261018.csv`
- `docs/operations/ema_modes_backtest_2026-10-22_fee3bps.json`
- `docs/operations/gamma_modes_backtest_2026-10-21.json`
- `docs/operations/snapback_timeframes_backtest_2026-10-22.json`
- `docs/operations/supertrend_mode_sweep_2026-10-22.json`

Current repository: `https://github.com/nageshmadaram/sterling` (private)

**Audit note:** These artifacts were read without testing the running backend or accessing authenticated broker sessions. Historical issue reports may be superseded by later code; remediation claims require re-verification on the exact deployed release SHA.

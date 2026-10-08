# Sterling production release audit — 5 October 2026

**Verdict: not ready for live capital.** The audited fixes pass the local regression suites, build and dependency checks. Live entry remains disabled. Snapback still lacks production broker lifecycle wiring, Gamma swing cannot represent its reference stop as broker-held protection, and deployment/broker acceptance has not been performed. Passing software tests does not resolve those requirements.

Scope: SuperTrend, Snapback and Gamma Unwind, plus shared account, execution, protection, alerts, transport and deployment boundaries. The audit starts at commit `559b51031f1d71c42d76012e1c1e8dbe19a9d153`, tree `45535bc00e95c700c6e3a0a12566eabf69e02690`. File digests and result provenance are recorded in [the evidence manifest](2026-10-05-production-release-evidence.json).

## Completed fixes and verification paths

| Boundary | Resulting behavior | Verification |
|---|---|---|
| Signals → replay → API → table | Repaired the backtest service syntax; exercised the real replay path; serialize an undefined/infinite profit factor as null instead of invalid JSON. Closed Snapback rows use recorded exit prices. | Backend replay/API tests, frontend adapters and board regressions |
| Account → session → entry controls | New production accounts require live credentials. Production refuses replay account creation and replay plan arming. Account mode stays immutable. The UI offers broker accounts and reports missing/unknown identity explicitly. | Account, environment seeding, production JWT and plan-admission tests |
| Readiness → AUTO | AUTO requires an explicitly live, connected account and a successful fresh readiness response with no blockers. Cached readiness cannot authorize AUTO after a refetch error. | Frontend control tests and backend release/safety tests |
| Fill → broker stop → trail → exit | GTT protection requires fresh signed inventory in the exact exchange/symbol/product bucket and a fresh broker quote. Orders have tick-sized LIMIT prices. MIS cannot borrow NRML inventory or be described as GTT protected. Existing position product identity survives recovery; new Gamma entries use NRML. | Actual Kite HTTP client exercised with MockTransport, product/recovery and lifecycle tests |
| Stop/target → submission | Refuse an already crossed stop or target, including a stop moved across the market by tick rounding. Preflight refusals do not create a false new-placement uncertainty fence. Persist intent immediately before a real submission; uncertain broker outcomes remain fenced for reconciliation. | Long/short placement and trailing regressions, durable protection and duplicate-exit tests |
| Lifecycle → Telegram | Restored missing async support and corrected pacing clock isolation. Queue, formatting, deduplication, retries and lifecycle alert regressions pass. | Telegram and lifecycle tests; no real bot/chat receipt was tested |
| API results → diagnostic UI | Removed canned market/cash/latency proof values. Diagnostics show the measured source and target. Replay GTT inventory cannot certify broker protection. | Backend diagnostic tests and frontend measured-provenance regression |
| UI consistency | Release engine controls and command navigation identify SuperTrend, Snapback and Gamma Unwind. Removed broker simulation selectors, shortened mode labels, and labelled historical execution as replay. Fixed invalid SuperTrend table animation CSS. | 1,610 frontend tests; TypeScript and production build |
| Application → proxy → stream | Enable WebSocket forwarding through Vite and the production nginx route. Add static cache policy and response headers; lazy-load the authenticated dashboard and split bundles. | Isolated frontend-origin API/WS test passes, including rejection of another tenant's subscription. Production nginx/container acceptance remains outstanding. |
| Dependencies → deployment | Pin a reproducible API dependency closure. Remove the unused legacy Kite SDK from the API runtime; isolate the optional evidence collector dependency. Upgrade frontend tooling and fix known dependency findings. Add CI compile, audit, data-lake, image and proxy checks. | Fresh backend install; dependency audits; build and compile checks |

Historical data and transport identities remain truthful. Renaming an offline label does not turn a replay fill into a broker fill. Legacy exposure recovery remains covered even though retired engines are excluded from release controls.

## Final local results

| Check | Result |
|---|---|
| Backend, fresh locked environment, Python 3.12.14 | **7,628 passed, 7 skipped**, zero failures/errors |
| Frontend, Vitest 5.0.3 | **1,610 passed**, zero failures |
| Data lake | **294 passed**, zero failures |
| Total | **9,532 passed, 7 skipped** |
| TypeScript + Vite 8.3.2 production build | Pass |
| Backend compile and `git diff --check` | Pass |
| API dependency lock audit | 51 packages audited; zero known vulnerabilities |
| Frontend dependency audit | Zero known vulnerabilities |
| Frontend-origin API + WS upgrade + tenant rejection | Pass in isolated test environment |
| Browser acceptance | Blocked: missing Chromium headless-shell; browser ZIP downloads failed; setup could not launch and 13 tests did not run |
| Production images/nginx, Python 3.13, CI Node 22 | Await exact-source CI; not verified locally |
| Live broker orders/fills and Telegram receipts | Not run |

Largest emitted JavaScript chunk: 189,595 bytes. JavaScript referenced by the initial HTML: 441,874 bytes across seven files. These are build sizes, not measured production latency. Local Node is 24.19.0; CI and frontend Docker use Node 22. The optional legacy study SDK is outside the API runtime vulnerability result. Deprecation warnings remain in the test toolchain.

## Requirements before enabling capital

1. **Complete Snapback's production lifecycle.** `snapback_plans._admission` refuses live arming because the strategy-owned position/protection adapter and dispatcher are absent. The injectable executor's test coverage is not production integration. Connect exact-account reconciliation, partial option/futures fills, hedge ownership, durable protection, recovery and exits, then prove them together.
2. **Resolve Gamma swing protection without changing the strategy silently.** The reference uses the D68 underlying daily-close stop and has no premium stop before T1. The live runner refuses this lane. A broker-supported protection design and an explicitly versioned strategy decision are required before this lane can enter.
3. **Pass the release gates.** `lane_registry.LIVE_EXECUTION_ENABLED` remains `False`. No environment variable or account switch can lift this floor. Economic/promotion and capital-permission gates remain in effect.
4. **Obtain evidence for the exact deployed source.** Require successful remote CI and browser/container acceptance; validated production authentication and account ownership; clock/static-egress/session checks; fresh executable quotes and margin; actual broker order/fill/GTT inventory reconciliation; restart, reconnect, partial-fill, rejected-exit and uncertain-submission recovery; and real Telegram delivery receipts. Record build/source identity and rollback instructions with the acceptance results.

No production host was deployed and no real broker orders were sent during this audit. The changes are suitable for review and further acceptance testing; this report does not certify a live-money release or trading profitability.

Broker contract references: [Kite Connect GTT API](https://kite.trade/docs/connect/v3/gtt/) and [Zerodha GTT products/support](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/gtt/articles/what-is-the-good-till-triggered-gtt-feature). GTT registration/triggering is not an exit fill; triggered LIMIT orders still require fill reconciliation.

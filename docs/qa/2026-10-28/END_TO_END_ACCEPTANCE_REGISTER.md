# Sterling end-to-end acceptance register — 2026-10-28

**Review scope:** latest GitHub `dev` integration, screenshot-grounded mobile defects,
read-only signal visibility, Space/Echo streaming, Web Push and Kite connection.
**Important:** This is a source review and regression-test register. Neither a
GitHub commit nor a unit test definition is proof of a successful deployed
end-to-end run. No live-entry promotion is authorized.

## Status definitions
- **SOURCE REVIEWED**: inspected code and committed a targeted change.
- **TEST ADDED / NOT RUN**: regression exists in tree; no execution receipt.
- **BLOCKED / NOT OBSERVED**: requires authorized backend, authenticated
  Kite identity, supported browser or real Android device.
- **FAIL (screenshot)**: supplied device image visibly contradicts acceptance
  (prior deployed build; retest required after deployment).
- **PASS**: reserved for a dated, reproducible execution result with exact
  deployed Git SHA, environment, observed outcome and evidence reference.

## Artifact-by-artifact acceptance

| # | Artifact / page | Screenshot / source issue | Source remedy | Acceptance evidence still needed |
|---|---|---|---|---|
| 1 | Shared mobile header (Kite) | header + banners reduce rows | compact status, collapse optional filters, resize navigation | 320/360/393/412/600px portrait + landscape screenshots |
| 2 | Shared mobile header (Space/Echo) | redundant sun between Search and Menu | route the same display/player actions to top of hamburger, keep Search visible | verify taps and menu state on Android |
| 3 | Hamburger and account panel | text wraps unevenly into one-word columns | responsive grid, min-width and text-wrap constraints | overflow, focus and touch audit at 200% font size |
| 4 | Workspace chooser | oversized fixed-like dialog | adaptive card padding, capped viewport height, scrolling | compact phone, short landscape and keyboard screenshot |
| 5 | Profile picture | hidden synthetic file picker can fail on phone | native transparent file-input touch target, crop preview, save remains explicit | camera/gallery selection, 10MB/format rejection, saved photo after refresh |
| 6 | Profile crop modal | fixed geometry and background touch | viewport height cap, inert background, scroll/focus rules | one/two finger crop, rotate/zoom, cancel, save, close and Android Back |
| 7 | Kite assisted OTP | immediate `done` not observed by poll-only refresh | handle state transition, invalidate account and authoritative status queries | **fresh broker** account-matched `/status` receipt after login |
| 8 | Kite account switch | login to inactive saved account differs from active broker session | explicit switch through existing exposure/order safety path | broker ID, zero/unresolved exposure and switching guard receipts |
| 9 | Kite mobile header/banner/footer | stale OFFLINE despite saved token | consistent saved-but-unverified vs connected statuses | same account identifier on 3 surfaces; no false CONNECTED |
| 10 | Today-first loader | lengthy status; one failed read hides unrelated history | concise default + expandable detailed status, isolate failed strategy | four current-day API timing and result receipts |
| 11 | SuperTrend | repetitive mode copy; old 1H display | compact original/default Swing text and detail disclosure | no redundant 1H mode; genuine 60m Swing behavior; fresh dated rows |
| 12 | SuperTrend history | empty after hours or 'Today only' | visible filters and Show all reset | current-day before historical; older rows with safe status |
| 13 | Snapback | saved scan modes hid other stored signal types | decouple scan choices from display rows | cross-mode persisted rows; no historical trade eligibility |
| 14 | Gamma-Unwind | no rows on closed days | restore/allow history once priority checks settle | generated job ID, scan timing, historic reconstruction |
| 15 | EMA Trend | unavailable or no selected NSE symbols | show explicit errors/config and safe quickstart path | scan config, saved symbols, API rows and paper-only guards |
| 16 | Columns / Modes / Best / Closed | filters permanently occupy mobile viewport | mobile disclosure + 'Show all' reset, keep scan actions outside | rows visible above fold; keyboard and screen reader |
| 17 | Signal alerts | generic 'Sterling update' and wrong tab | include event text, validated /kite route, strategy/signal parameters | Android push delivery, notification content and exact signal modal |
| 18 | Push service worker | old '/' URL leads chooser/invalid route | safe origin/path, same-origin /kite routing, navigate window before focus | background, locked phone and terminated-browser taps |
| 19 | Space video | slow first frame, buffering, static aspect | smaller HLS startup buffers, playback before queue render, responsive player | median/P95 tap-to-first-frame; 4G/Wi-Fi/channel changes |
| 20 | Echo radio | slow channel switch and catalogue blocking | audio request before remote catalog; faster failover and smaller buffers | median/P95 station-switch-to-audio, codec and stale-stream cases |
| 21 | TV expanded player theme | dark gradient under Light | light palette overrides on `#tv-now-playing` controls | Light/Dark/grey UI screenshot, contrast and focus |
| 22 | Radio expanded player theme | dark gradients under Light | light palette overrides on `#mobile-player-sheet` controls | Light/Dark/grey portrait and landscape screenshot |
| 23 | Media zoom and viewport | static dimensions, unexpected pinch | scoped pan-only touch action inside media iframe and `dvh` geometry | Android viewport-resize/orientation/keyboard/gesture tests |
| 24 | Workspaces / navigation | profile/sidebar and close-page inconsistent | shared account sheet, header action moved, mobile layout | page-by-page visual diff across Kite/Space/Echo/admin |
| 25 | Order execution/notification safety | history may be mistaken for executable | read-only notification navigation and existing independent order fences | zero-order shadow mode; authorized broker simulation drills |
| 26 | Backend security | tenant/leakage/mutation concerns | existing auth and safety gates unchanged | authenticated cross-tenant denial, SSRF, replay/dedupe and audit log |
| 27 | Tests / CI | source tests committed but no run proof | focused Vitest/Pytest tests for modified paths | exact commit build + complete JS/TS and Python test exit codes |
| 28 | Deployment | screenshot showed earlier build `8fa98e3f` | GitHub merge only; no host access | currently served Cloudflare/localhost manifest SHA and smoke receipt |
| 29 | Git hygiene | user wants only main/dev, no local dirt | main/dev/third fast-forward synchronized | local checkout status, deletion of extra remote/local branch via authorized host |
| 30 | Trading release / profitability | no sufficient forward evidence | live-entry remains OFF | approved broker identity/IP, protection/recovery drills, after-cost forward outcomes |

## Verification evidence format

For each item: `{test_id, exact_sha, deployed_url, device,
OS, browser, viewport, network, start_timestamp_IST, observed_result,
expected_result, elapsed_ms, log_or_screenshot_reference,
pass_fail, operator_identity}`.

Media performance must use real first audible/first decoded frame, not
catalogue-loaded / manifest-parsed timestamps. Store median and P95 across
at least ten channel changes per network; log failed starts as failures rather
than dropping them from the sample. No synthetic timings or pass flags.

## Release rules

1. Never mark `Kite login saved` as confirmed broker connectivity without
   checking active account and current broker validation.
2. No evidence that a scan *responded* should be used to infer a signal was
   generated, filled or profitable.
3. Viewing historical signal rows must not authorize an order.
4. All broker-facing live-entry gates remain OFF pending independently
   authenticated execution, stops, recovery and economics receipts.
5. Do not request actions from the hospitalized operator; device/host QA is
   delegated to an authorized healthy operator/environment when available.


## 2026-10-28 focused mobile media zoom execution receipt

**Implementation:** `frontend/public/media/mobile-no-zoom.js`,
Git blob `e54d175031e678f8cb5e977705790bcf43c23ecb`, is loaded
in the head of **both** `backend/app/media_templates/tv.html` and
`backend/app/media_templates/radio.html` and works only with a viewport
width of 1023px or less. Both embedded documents set a fixed-scale viewport.
Single-finger swipes and ordinary controls remain usable. The parent Sterling
app is not modified by the iframe-specific gesture listeners.

**Observed source-level execution:** The exact GitHub JS blob was executed in
a mocked document/window V8 environment in the current session. **14/14
gesture assertions passed**, covering mobile/desktop single-finger movement,
two-finger pinch start/move, iOS Safari gesture events, double-tap default,
Ctrl+wheel zoom and ordinary wheel. Listener configurations were also verified
as `capture:true, passive:false`. Both served templates were re-read to verify
their script and viewport integration; Radio's separate
`/radio/appearance.css` and TV's inline mobile style include their touch rules.

**Regression source:** `frontend/tests/mobileMediaZoom.test.ts` added to the
repository to test the actual shared asset and both template references.
**Vitest run on this exact commit is NOT VERIFIED.**

**Still required to mark device PASS:** On a supported Android device test
portrait and landscape TV and Radio under the deployed exact SHA, attempt
two-finger pinch and double tap on artwork, scrolling content and media
controls; confirm there is **no page-scale change** and that station/channel
switching and single-finger scrolling still work. This cannot be certified from
a simulated event dispatch.


## 2026-10-28 CI rerun receipt — certification remains NO-GO

**Exact tested source SHA:** `bf153f8f2b70f38520d65ab5c6c7425b305c2ffb`
(at the time these jobs were requested). These are GitHub Actions
job-execution receipts, **not** successful application test results.

| Workflow | GitHub run ID | Attempt | Conclusion | Runner/steps |
|---|---:|---:|---|---|
| CI | 40598169088 | 2 | FAILURE | empty runner name, 0 steps for Type Check, Vitest, Python 3.12/3.13, mobile-checks, Docker Smoke and Sterling Release Gate |
| Snapback release gate | 40598169055 | 2 | FAILURE | no runner-assigned steps |
| Regression Gate | 40598169031 | 2 | FAILURE | no runner-assigned steps |

The repository-connected action successfully requested reruns of all failed
jobs for all three workflow runs. The jobs completed with failures again
within seconds. CI run attempt 2's Type Check job, for example, started
`2026-10-28T02:28:53Z`, ended `2026-10-28T02:28:56Z` and
records `runner_name=""`, `steps=[]`. No actual npm test, Python,
Docker or Playwright execution is evidenced. Job-log downloads return
404 missing-blobs through this connector. The check-run metadata reports
annotations, but their URL is not authorized for retrieval.

**Blocker tracking:** https://github.com/nageshmadaram/sterling/issues/276

**Result:** NO-GO — both full test evidence and real Android/broker evidence
are absent. This is not proof that any individual code-level test fails; it
shows the configured GitHub runner has not executed it. Do not remove
regression jobs, fake their receipts or bypass live trading safety.
Source changes committed after this exact SHA are also untested by these
workflows and require a new exact-SHA run once runners work.

**Unresolved environmental dependency:** GitHub Actions organization/account
policy, capacity, or billing/usage must be examined by an authorized
repository administrator. The true root cause has not been identified, so
no billing diagnosis should be asserted as fact.

### Remaining certification evidence, in strict sequence

1. Healthy CI runner -> complete frontend build/typecheck/Vitest and backend
   Python tests against one pinned SHA, archived artifacts and exit codes.
2. Backend and frontend start/health/contract checks, then authenticated
   endpoints and tenant-isolation drills; record API/WS latency metrics.
3. Real Android portrait/landscape on the deployed exact SHA: page-by-page
   Light/Dark visual proofs, profile upload/crop, Space/Echo zero-zoom,
   first-frame/first-audible timing with network profiles, and history/signal
   scanning states.
4. Real mobile push delivery in background and lock screen, including exact
   strategy/signal deep link and notification payload with no credentials.
5. Authorized broker identity validation for the intended account; exposure
   must be readable and confirmed, plus independent stop/exit/restart proofs
   under approved safety guards.
6. Exact-SHA isolated backup/restore, disconnected/reconnected drills, final
   immutable manifest and release authority review.
7. Forward after-cost economic data with an appropriate out-of-sample period.
   No promise of profits. Live-entry permission remains off pending acceptance.

All missing items remain **BLOCKED/UNVERIFIED** until their real receipts exist.

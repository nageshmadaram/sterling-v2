# Sterling Radio / Space / Administration verification register
Date: 2026-10-28
Branch: codex/radio-tv-admin-ux-20261028
Base: 6dea02f42afd307d3db38fb79518631793be35cb
PR: https://github.com/nageshmadaram/sterling/pull/293

**Evidence classification:** SOURCE-INSPECTED and PATCH-COMMITTED are not EXECUTED or CERTIFIED. No local host, phone, authorized broker session, production deployment or notification receipts were available to this GitHub-only change.

| Artifact | Specific acceptance | Current evidence | Release state |
| --- | --- | --- | --- |
| Admin: Add User | Create radio/TV-only user with `features` and settings `configuration`, persist grants, sign in as user | Main API schema supports both; deployed API reportedly returned `extra_forbidden`; UI now diagnoses mismatch without stripping grants | BLOCKED: verify/redeploy active backend then execute |
| Account menu | Station sources, default station, preferences open in foreground with focus, changes persist | Outer account dialog closes before deferred player command; no browser receipt | NEEDS BROWSER QA |
| Radio: expanded settings | Top-right options opens consolidated radio features, EQ shortcut remains available | Command routing and button committed | NEEDS BROWSER QA |
| Radio: startup vs equalizer | At most one settings sheet shown; backdrop, Escape, gesture close | Source and added runtime unit tests; tests not confirmed passing | NEEDS TEST EXECUTION |
| Wind Down | Theme matches, drag-down dismisses and restores focus | Shared gesture, CSS, focus code committed | NEEDS MOBILE QA |
| Radio: player geometry | Artwork maximum usable size, station title/subtitle spaced, central playback glyph | Responsive rules committed | NEEDS VISUAL QA |
| Radio: initial page | Every fresh authenticated Radio open lands on All; no previous category leakage | Existing HTML initial `data-radio-category=all` and hydration handling inspected | NEEDS BROWSER QA |
| Radio: search | No-space station match and one-edit typo suggestions, no stale network overriding | Search implementation and regression specs added | NEEDS TEST EXECUTION |
| TV: search | No-space match, typo fallback and suggestions | Search implementation and regression specs added | NEEDS TEST EXECUTION |
| Radio & TV: light/dark | Menu sheets, text and artwork/video backgrounds consistent on desktop and phone | CSS harmonization committed | NEEDS VISUAL QA |
| TV: video edges | Native video and iframe fallback clipped to one rounded surface, no edge border | CSS committed | NEEDS VISUAL QA |
| TV: suggestions | Narrow stable desktop rail, concise titles and 100px thumbnails | CSS committed | NEEDS VISUAL QA |
| Media: header | Desktop search center; optional hide header/restore full playback height | Host header controls and styles committed | NEEDS BROWSER QA |
| Media: workspace cycling | Clicking Echo or Space cycles only allowed grant destinations; guards dirty settings | Permission-checked callback and test added | NEEDS TEST EXECUTION |
| Media: pointer | Pointer hides after 10s of playback inactivity and returns on movement/interaction | Playback-aware listener committed | NEEDS BROWSER QA |
| Radio & TV: responsive | Portrait and landscape scrolling, safe areas, sheets and artwork fit | Targeted CSS committed | NEEDS REAL PHONE QA |
| TV: startup latency | Real device TTFF measured from navigation to decoded frame, with stream and network recorded | No measurement | UNKNOWN |
| Radio: startup latency | Real device user-gesture to audible PCM, with station and network recorded | No measurement | UNKNOWN |
| Notification: exact signal | `Sterling · exact signal details · 18:30:13` arrives on physical phone, taps to correct signal with Setup chart and Option chain | No delivery/device/navigation receipt | BLOCKED |
| Kite: login | Fresh Kite authentication, verified broker binding and authorized session | No authenticated broker access | BLOCKED |
| Trading/strategies | End-to-end admission, entry, stop/protection, reconnect, fees, replay and economic proof | No live trading execution evidence | LIVE OFF |
| Build | Frontend production build, typecheck and media-style generation | No local execution; CI result requires review | NOT CERTIFIED |
| Unit and integration | Relevant Vitest, Python and media runtime regressions green | New tests committed, no passing report | NOT CERTIFIED |
| E2E desktop | Page-by-page Playwright browser screenshots/interaction, dark and light | No execution | NOT CERTIFIED |
| E2E phone | Portrait/landscape playback, touch, overlay, screen capture, notification open | No execution | NOT CERTIFIED |
| Deployment | Same address serves matched frontend bundle and backend API contract/version | Neither active process nor revision verified | BLOCKED |

## Deployment sequence (safe for ongoing local rescue work)
1. Preserve uncommitted rescue changes and identify current host HEAD; do not fast-forward blindly.
2. Review PR #293 against the local rescue branch; resolve overlaps rather than overwriting.
3. Run existing `frontend` media-style build, frontend production typecheck/build, relevant Vitest and Playwright suites, backend API contract tests. Capture failures verbatim.
4. Check server routing at the active address: backend process commit, API payload schema and auth/session; correct stale deployment before retesting Add User.
5. Apply one tested release to frontend and backend together; invalidate cached assets only after matching server build is live.
6. Capture reproducible desktop and phone evidence for every UX row; preserve the Kite/notification gates and immutable execution receipts.
7. Keep all live trading paths disabled until the independent broker and protection certification register is green.

## Failure discipline
- A CSS diff or a passing static inspection is not evidence of a visually correct phone.
- GitHub Actions job failure is not proof of a code regression until job logs are available.
- No fake performance measurements, notification acknowledgements or Kite connection claims.
- Do not discard permissions to make stale API user creation appear to succeed.

## Continuation audit: confirmed baseline receipts vs new changes

**Existing baseline execution (application `862c03a53e6e2ec6742d7099b0696c118037d7f7`):**
- [Source receipts](../acceptance/README.md) record 2,505 passing frontend tests, 12,585 backend tests (30 skipped), 33 media browser journeys, and a 743-capture, seven-width Light/Dark page/overlay sweep. These are historical receipts for the earlier application release, not retests of this PR.
- [Controlled media timing](../acceptance/controlled-media-timing.json) covers ten local WAV and HLS emulated sessions per player: Radio median/P95 58.9/80.2 ms; TV 89.5/140.2 ms. These numbers are **not** phone, actual broadcaster, 4G, or speaker startup measurements.
- [Admin acceptance](../acceptance/admin-create-browser.json) records real browser -> API -> disposable SQLite creation, permission persistence, and a synthetic test-user sign-in. This is not evidence that the production Add User request from the report succeeded.
- [Exact notification](../acceptance/exact-signal-notification.json): push provider accepted one request; `phone_observation` remains `pending`. [Browser signal](../acceptance/exact-signal-browser.json) opened the NIFTY FIN SERVICE detail panel with Setup chart and Option chain controls; this is not the exact notification being opened on a phone.
- [Kite runtime](../acceptance/runtime-final.json) records a connected account and its specific backend/profile verification, while the status poll showed `validated=false`. This historical account receipt does not certify a fresh Kite connection on this PR.

**Important version nuance:**
- Remote `rescue/sterling-20261028-142648` is **44 commits behind** main and contains the old Add User schema. GitHub `main` and `dev` contain `UserCreate.features` and `UserCreate.configuration` with corresponding credential-service grants.
- The earlier runtime receipt names backend source `1960ace82`, whose GitHub source also contains those fields. Therefore, the historical `extra_forbidden` response cannot be attributed to the **currently recorded runtime** without a new runtime routing/worker check. A stale worker, different backend target, or request made before deployment remain hypotheses.
- Read-only probe: `python3 scripts/verify_user_create_contract.py` checks the actual loopback backend's `/openapi.json` to distinguish an old contract from current source without creating an account or transmitting credentials. An inaccessible schema is UNKNOWN, not PASS.

**Additional PR-only work / scoped checks:**
- Radio's header and expanded-player options use delegated `data-radio-click="82"` rather than inline `onclick`, retaining control under a restrictive script policy. The hosted `more` command uses the same Extras panel.
- Startup/DSP overlays are mutually exclusive before opening Extras; native Extras swipe closure restores focus without disturbing another dialog. Portrait Radio no longer reserves blank viewport height beneath controls.
- Actual extracted JS Radio/TV search functions were executed against deterministic fake station/channel records: normal, omitted-spaces, and one-character spelling errors returned the expected station/channel. This is isolated function execution, not an end-to-end application test.
- All modified source JS and player-template inline scripts parsed without JavaScript syntax errors. Basic CSS brace-balance checks passed. These checks do not prove TypeScript, Vitest, Playwright, visual design or hardware playback.
- New regression tests exist in source; GitHub CI, regression and Snapback workflow results for this PR are FAIL. The connector's job log fetches reported BlobNotFound/absent logs. Do not classify the failures as source defects or infrastructure failures without the missing logs.

**Release decision:** NOT CERTIFIED; retain PR as draft, do not merge/deploy or enable live orders until the active runtime and tests are verified. Preserve the local rescue working tree.

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

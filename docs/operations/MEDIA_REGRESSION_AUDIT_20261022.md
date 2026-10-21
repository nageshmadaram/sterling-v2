# Radio / TV regression audit — 22 October 2026

Scope: hosted Radio and TV, standalone exports, shared artwork/comfort services,
host command and preference bridges, owner-scoped persistence, mobile navigation,
queues, asynchronous playback, podcast requests, focus, release gateway and build.
Graph MCP tools were unavailable; source, runtime and browser checks were used.

## Confirmed findings and fixes

| Finding | Effect | Correction / regression evidence |
|---|---|---|
| Radio retained an old queue index after direct selection | Next / Previous could skip to a different station | Resolve the active station in the current queue; unit regression |
| Radio late HLS callbacks ignored explicit pause | A manifest or fatal error could restart audio after sleep | Guard asynchronous play and fallback by pause intent; late-manifest regression |
| TV asynchronous autoplay lacked pause intent | A pending play rejection could retry muted after sleep | Explicit pause state shared by controls, sleep and Media Session, including embedded fallbacks; deferred-promise / embed regression |
| TV had two competing HUD timers | Earlier callbacks could hide newly revealed controls | One timer shared by pointer/tap/focus events |
| Nested feature menus overwrote return focus | Closing a submenu focused detached content | Keep a separate originating-player focus target; Chrome regression |
| Comfort dialog treated padding as backdrop | Tapping empty space inside dismissed the sheet | Check backdrop coordinates against dialog bounds; Chrome regression |
| v13 migration occurred on TV mount without ownership checks | Another account could revive an old browser library | Remove automatic restoration of unowned v13 cache; retain owner-scoped v14 sync and server tombstones; unit regressions |
| Malformed library members and counters survived parsing | Saved null/string entries could crash player initialization | Validate item shapes and numeric settings; malformed Radio and standalone TV regressions |
| Standalone TV depended on an absent React host | Startup and persistent options did not match hosted behavior | Standalone-only bootstrap and namespaced storage; hosted document remains memory-only; reload regression |
| TV regeneration accumulated blank lines | Exports drifted on each rebuild | Replace marker blocks in place; repeated hash-equality artifact test |
| Optional artwork tags could be a non-array | One malformed row discarded all metadata | Validate optional tags before choosing square logos; metadata regression |
| TV reported clipboard success before completion | Permission-denied sharing falsely claimed a copy | Await write and report failure truthfully; denied-clipboard regression |
| TV preference limits counted characters while the server counted UTF-8 bytes | Large Unicode settings could pass the bridge but fail every database save | Align host/iframe byte budgets; Unicode regression |
| Podcast requests lacked cancellation and generation ownership | A slower previous show could replace current episodes | Abort/deadline/generation guards, retry UI and episode-specific navigation; stale-response regression |

## Artifact chain

- `frontend/public/radio/index.html` → compiled shared assets →
  `sterling-radio/sterling-radio.html`.
- `frontend/tv-mobile/{mobile.js,mobile.css}` → `scripts/embed_tv_mobile.py` →
  `frontend/public/tv/index.html` → `scripts/export_sterling_tv.py` →
  `sterling-iptv/sterling-iptv.html`.
- Both hosted documents retain opaque iframe isolation. Standalone persistence
  is injected only into the exported TV artifact and still checks top-level mode.
- Existing UI preference keys and database allowlists remain unchanged.

## Verification and limits

The audit adds focused runtime, ownership/migration, artwork, browser, and
artifact reproducibility regressions. Existing theme, search, queue, 3-hour
availability, forced retry, scrolling, timer, PiP-dispatch, and portrait flows
are rerun. Gateway CSP and backend preference isolation checks are included.

This does not certify every public broadcaster stream, authoritative EPG data,
physical iOS/Android autoplay, background execution, or native PiP permissions.
Offline-controlled browser fixtures deliberately avoid broker accounts and
external broadcaster availability. Physical-device and live-provider behavior
remain external acceptance checks; no unverified FPS or playback claim is made.

Verified: 82 frontend regressions, 19 Chrome browser flows, six backend
preference tests, and one real gateway isolation test passed. Production
build passed. Eight ownership tests were repeated after tightening legacy
cache restoration.

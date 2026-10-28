# Media refinement execution evidence — 28 October 2026

The application changes are committed at `80deaff447cc01d479ad5bcdbc0d1aab9cfe7d56`.
Local immutable publication on port 5180 and the currently verified public
origin served this revision. The backend was restarted on the Python sources at
`182f622c2`; subsequent application changes through `80deaff44` are frontend/media
changes. Test and audit harness corrections recorded alongside this bundle do not
alter application behavior. Earlier acceptance evidence remains historical.

**Status: automated browser/API checks passed; full backend rerun in progress;
physical-device visual acceptance remains open.** The operator reports roughly
2–3 seconds to playback but says the UI still looks off and will attach
screenshots. Phone push re-registration and exact-detail navigation were explicitly
deferred to the [follow-up TODO](../../../qa/2026-10-28/FOLLOW_UP_TODO.md).

| Check | Evidence and scope |
|---|---|
| Frontend suite | [2,517 passing tests in 280 files](frontend-tests.log). Final complete run used two workers after an earlier high-contention five-second test timeout. |
| Build | [TypeScript and Vite passed](build.log). The publication manager also built and served the immutable application revision. |
| Media browser | [40 passing browser journeys](media-browser-tests.log): playback state, contrast, corners, sheets, first-load Home, sizing and scroll behavior. This run preceded the final TV native-dialog migration; focused template tests and actual-host handoffs below cover that migration. |
| Focused templates/dialogs | [45 passing tests](dialog-tests.log), including generated-export consistency and native TV settings dialogs. |
| Actual host settings | [Four passing phone/desktop radio/TV handoffs](host-settings-handoffs.json) on the published revision: account menu closes, startup/source fields accept text, tools open in the foreground. External streams and private settings writes were blocked. |
| Workspace authorization | [28 passing focused tests](workspace-policy-tests.log). The [initial full backend run](backend-initial-run.log) had 12,595 passes, 30 skips and one stale test expecting a pending request for already-granted radio; the corrected assertion also checks no redundant request row is created. No authorization behavior was changed to satisfy the test. A fresh full run is still in progress. |
| Gateway containment | [Six passing tests](gateway-tests.log). |
| Offline admission/recovery | [42 passing tests](offline-admission-tests.log); fake broker and isolated processes, not actual trading recovery certification. |
| Page/overlay sweep | [742 captures at seven widths](visual-audit.json): 320, 360, 393, 412, 600, 844 and 1440, both themes, member/admin pages and overlays. No document overflow or uncaught browser errors. Desktop was rerun after correcting the audit's obsolete search-button locator. This is coverage and geometry evidence, not a claim that every captured pixel is approved. |
| User creation | [Browser → production handlers → disposable SQLite](admin-create-browser.json): all three workspace grants, independently hidden TV discovery, seven settings permissions, successful new-user sign-in. No production test user was created. The isolated build's immutable release identifier was not asserted. |
| Deployed create schema | [Three live contract probes](live-user-contract.json) accepted features/configuration/visibility; invalid username rejected before mutation. Backend, development gateway and public origin agreed. |
| Real Kite connection | [AA0595 broker profile HTTP 200](kite-live-profile.json), 17:29 UTC, read-only. No orders were placed. |
| Retired push origin | [One old-origin registration disabled](retired-push-origin.json). No registration was migrated across origins; phone re-enrolment is deferred. |

[Controlled media samples](controlled-media-timing.json) use a generated WAV and
HLS stream in mobile Chrome emulation: ten samples per player; radio median/P95
116.8/173.4 ms, TV 163.1/225.5 ms. TV measures the first decoded frame; radio
measures the `playing` event. These are not phone-speaker, broadcaster or mobile
network latency measurements. A synthetic radio station with no related catalogue
entries cannot scroll its title completely away; its compact strip correctly stays
hidden. Dedicated populated-catalogue browser tests cover strip appearance.

## Representative captures

| Player | Light | Dark | Below the first screen |
|---|---|---|---|
| Radio | [Player](radio-light.png) | [Player](radio-dark.png) | [Details](radio-light-scrolled.png) |
| TV | [Player](tv-light.png) | [Player](tv-dark.png) | [Details](tv-light-scrolled.png) |

[Radio tools](radio-tools-393-light.png), [TV tools](tv-tools-393-light.png),
[initial user access](admin-create-access-393-light.png) and
[radio appearance menu](radio-display-393-light.png) document representative host
and embedded surfaces. Retained desktop layout rollback, centered desktop search,
workspace cycling, matching player corners and palette, native modal sheets, and
shared scroll cards are source-verified; physical phone rendering still requires
the operator's screenshot review and retest.

Retained logs have trailing whitespace normalized; raw execution logs remain in
`.runtime/acceptance-20261028`. The bundle contains no sessions, credentials, push
endpoints, databases or backups.
Its manifest hashes the retained evidence; it does not certify unobserved device
behavior, CI environments, or live-entry readiness.

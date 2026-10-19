# Sterling Spectrum — TV integration and audit (2026-10-20)

## Intended operator experience

- The persistent product header is **STERLING | KITE | TV**.
- KITE is the default workspace; its existing trading navigation, mode
  selector, settings, account profile and operational layout remain intact.
- Selecting TV shows the IPTV application across **all available space beneath
  the Sterling header**: no KITE activity rail, trading docks or status footer.
  The embedded IPTV search, sources, themes and player tools open from a
  small "TV tools" overlay button, rather than consuming another permanent row.
- Switching away from TV unmounts its iframe and stops its page lifecycle.
  The KITE workspace is kept mounted but visually hidden while TV is active;
  its background monitoring is intentionally not disabled by a media tab.
- Unsaved trading settings require confirmation before switching to TV.
- KITE and TV are top-level Spectrum products, not new strategy tabs or a
  variant of live/simulation. The owner-only live/simulation switch remains
  separately available on the KITE screen.

## Files and delivery

- `frontend/src/pages/SimpleTerminal.tsx`: product tabs and workspace mounting.
- `frontend/src/components/sim/ViewSwitch.tsx`: optional LIVE/SIMULATION label
  in the KITE header; default public behavior remains the previous KITE label.
- `frontend/src/components/tv/TvWorkspace.tsx`: full-height sandboxed player,
  allowlisted TV-only preference bridge and load/error states.
- `frontend/src/styles/terminal.css`: responsive Spectrum tabs.
- `frontend/public/tv/index.html`: **authoritative runtime IPTV application**,
  derived from the original `sterling-iptv/sterling-iptv.html`. The original
  file remains unmodified as a source snapshot; the runtime adaptation lives
  under the frontend's public folder, bundled into Vite static output.
- `frontend/nginx.conf`: isolated TV CSP, opaque-origin sandbox (including
  direct navigation), media/CDN/worker allowances and `/tv` redirects.
- Frontend TV component and source-security unit tests.

## IPTV security decisions

1. The iframe uses `sandbox="allow-scripts allow-forms allow-downloads
   allow-presentation"`, **not** `allow-same-origin`; child scripts cannot
   access Sterling's localStorage, cookies, broker data or page DOM.
2. The Nginx route **also** applies CSP `sandbox` to prevent direct browsing
   of `/tv/index.html` from bypassing isolation. A small early redirect
   protects direct navigation in Vite development mode. The normal trading
   SPA CSP stays unchanged.
3. Because opaque-origin iframes cannot use browser localStorage, TV persists
   **only** these keys through a source-checked postMessage bridge:
   `streamos_sources_v13`, `streamos_source_mode_v13`,
   `streamos_favs_v13`, `streamos_stats_v13`,
   `streamos_history_v13`. The parent stores them under
   `sterling.tv.` and never forwards non-TV keys or private auth data.
   Limits apply to individual values and early boot writes are rejected until
   saved preferences are hydrated. No server-side TV account sync is added.
4. Automatic third-party public CORS proxy relays have been removed. Playlist
   URLs can contain private tokens and must not silently be sent to
   `corsproxy.io` or `allorigins.win`. Browser CORS and HTTPS
   mixed-content restrictions still apply; unsupported sources can fail.
5. Playlist-derived values used inside generated JS event-handler/HTML
   attributes have been escaped for both syntax layers.
6. The original generated EPG is **illustrative**, not real broadcaster
   schedule data; displayed programme titles are labelled `Guide preview`.
   The hardcoded `98% Match` recommendation figure was removed.

## Verification gates

- Previous EMA live-stop/GTT suite: **264 passed in 10.19s**, operator output
  at local merge `d91f48b9`. This is an **offline** certification only.
- TV changes: committed, but **local frontend TypeScript build, Vitest, browser
  viewport/media playback and deployed Nginx tests are NOT YET PROVEN**.
- Run `cd frontend && npm run build`, then run the three new TV test
  files and existing ViewSwitch tests.
- In the dev URL, confirm TV appears beside KITE, select TV and verify the
  iframe fills the space under Sterling's header. Use "TV tools" to search,
  manage IPTV sources and change themes. Switch back to KITE and verify
  original workspaces and notifications. Check mobile sizing separately.
- On a deployed Nginx release, verify
  `curl -I https://<host>/tv/index.html` returns the dedicated sandbox CSP
  and `/tv/` redirects to the isolated route.

## Open risks and deliberately deferred work

- IPTV requires third-party CDN scripts (Tailwind, HLS.js) and broadcaster
  stream hosts; availability, permission to view, CORS, autoplay and external
  metadata cannot be guaranteed. CDN scripts are isolated by an opaque-origin
  sandbox, but long-term production hardening should self-host pinned bundles.
- The M3U and curated channel catalogue remains legacy HTML/imperative JS.
  It has not been fully converted to typed React components. The archived
  HTML is not an additional runtime entry point.
- The inherited UI still has decorative "verified" / viewership labels
  which are not independently certified. Do not treat them as live metrics.
- Test actual media playback on a browser after deployment; unit tests
  cannot prove third-party channels work.
- EMA remains disabled for real-money entries. Broker GTT child-fill adoption,
  canonical signed ledger reconciliation, cancellation/oversell prevention,
  and operation/deployment certification remain release blockers.
  CI/CD stays deferred until the end of the Sterling release as previously
  directed.


## Release follow-up: 20 October 2026 — Node gateway CSP and stale build

The operator's pasted build log at `c948bf5f` establishes:
- `npm run build` **PASS**: 428 transformed modules, 1.13 s Vite build,
  with only a non-blocking >500 kB dashboard chunk warning.
- TV frontend: **20/20** selected tests PASS across 4 suites; the owner-denied
  ViewSwitch case emitted a non-fatal React `act` warning.
- Loopback Vite `http://127.0.0.1:5174/tv/index.html` served the TV file.
- The live gateway on `http://127.0.0.1:5180` was healthy but retained
  `release_commit=8ffd0d40`, `release_stale=true` and
  `release_error=CalledProcessError`. A dev-route check is **not**
  proof that this published gateway contains the latest TV files.

The actual production gateway is `scripts/serve_frontend.mjs`, not only
`frontend/nginx.conf`. This was a separate TV security/playback bug:
the Node gateway formerly assigned the trading SPA's default CSP to the
IPTV page. This blocked third-party media resources, and direct TV
navigation did not receive an origin-opaque policy. The Node route now
explicitly redirects `/tv` to `/tv/index.html`, applies a dedicated
sandbox CSP for direct and embedded visits, admits streaming media/HLS
workers inside that sandbox only, and preserves the original trading CSP.
An isolated `node --test scripts/tests/tv_gateway.test.mjs` test exercises
a separate ephemeral gateway and proves these headers without contacting
the trading backend. Additional Vitest source tests enforce route parity.

**Verification of these new gateway changes remains pending local tests.**
The running Node gateway loads server source at process startup. Publishing a
new static build alone does not reload the gateway's CSP source; refresh only
the presentation gateway through the safe Sterling supervisor after release
validation, without stopping an in-flight Kite trading backend.

The `CalledProcessError` root cause is *not established* by the pasted
logs. Sterling intentionally compiles committed source separately from the
working-tree build, logs failed `npm ci` or `npm run build` output to
`.runtime/stack/release-build.log`, and retains the last good published
release on failure. Inspect that log and free disk space before any
destructive cache clear. Do not force-publish a dirty frontend or flip the
live order switch.


## Spectrum Experience follow-up: restore legacy KITE UI and optional navigation

After the TV release the operator specifically requested the **original KITE CSS**
and original KITE↔Simulation switch, not a new pill-style KITE tab. Restored:

- `frontend/src/styles/terminal.css` is byte-identical to the pre-TV source
  at `d91f48b9` (`ba8dad7e...`).
- `frontend/src/components/sim/ViewSwitch.tsx` is restored to the exact
  pre-TV component (same classes, semantics, owner gate and unsaved draft
  check). TV and optional standalone destinations use the same
  `mobile-view-toggle unified-view-toggle` classes and remain immediate
  children of the Sterling header, so original responsive CSS applies.
- `Settings → Experience → Sterling Spectrum` and the compact settings
  drawer expose browser-local configuration:
    1. `toggle` (DEFAULT): original KITE⇄Simulation toggle plus TV button.
    2. `separate`: KITE / Simulation / TV as individual destinations.
    3. `cycle`: one button cycling enabled destinations in the preferred order.
  The three destinations support visibility, ordering and a validated
  24-character TV-only display name. Reset Spectrum settings restores
  precisely the default without resetting unrelated theme/navigation options.
- Simulation is subject to the existing owner status gate; switching between
  KITE and Simulation uses the unchanged account-view store and existing
  session isolation, not a fake TV-mode implementation. Navigation changes
  never enable real broker writes.
- The visible destination checks refuse hiding *both* KITE and TV. A hidden
  live KITE tab does not remove the route to separately visible Simulation,
  and TV-only mode offers an Experience recovery control.

Operator-provided verified baseline at `834713fa` prior to these changes:
frontend build succeeded; TV/header tests **21/21**; isolated Node gateway test
**1/1**; EMA offline regression **264/264**; published release was current
(`release_stale=false`). However, the RUNNING published Node gateway still
reported the **old trading CSP for /tv/index.html** because release-file
promotion does not reload gateway source code; this requires a supervisor-
controlled presentation-gateway restart independent of trading backend.

**The Spectrum customization commits are not yet tested locally.** Verify
frontend TypeScript/Vite, existing header + TV tests, new Spectrum settings
tests, Settings → Experience integration, and all three layouts on desktop
and mobile before calling this UI change released. Disk reported 97% usage
with 4.2 GB free; retain logs and backups, do not clear them blindly.


## 2026-10-20 — User-pushed IPTV v14 update and KITE/TV underline separation

A new `main` commit `bee47d4a` updated **only**
`sterling-iptv/sterling-iptv.html` (source snapshot). The formerly published
`frontend/public/tv/index.html` still contained the previous embedded player,
so the new startup-channel, language/Indian-TV selection and mobile-player
work was not yet visible in Sterling.

The runtime IPTV document has now been **re-derived from the exact updated
source snapshot**, with the retained Sterling integration:

- The v14 startup channel is hydrated from local TV preferences **before
  first playback**; M3U synchronization happens after initial playback. The
  five previous v13 TV preference values are migrated to v14, with a sixth
  preference key for `streamos_startup_channel_v14`.
- Retains user-pushed v14 channels, Indian-language catalogs, startup
  selector, mobile experience and hover previews.
- The iframe still has an opaque origin, no broker/API auth access, no
  top-navigation permission, and only allowlisted TV-key `postMessage`
  persistence.
- Automatic CORS relays for M3U playlists, live streams and hover previews
  remain disabled: private stream links are not sent to public proxy services
  without permission. As a tradeoff, browser CORS or mixed-content failures
  cannot always be recovered.
- The IPTV toolbar is kept accessible from the floating `TV tools` button;
  Sterling's header is the only persistent header.
- Template-based programme guides remain clearly marked as previews, and
  fabricated concurrent-viewer estimates are not rendered in the active
  player subtitle.

The original KITE component and original `terminal.css` remain byte-for-byte
unchanged. The additional rule in `globals.css` adds **10px inline-start
spacing only when one original-style Spectrum button directly follows another**,
separating their underline strokes without modifying KITE typography, color
or active-state rule.

New TV unit/static regression tests cover the v14 allowlist, v13 migration,
startup-channel persistence, integration markers and tab-gap style.

**Verification pending:** run frontend `npm run build`, selected TV and
Spectrum Vitest suites, the isolated gateway CSP Node test, and browser checks
for the v14 player and mobile viewport. There has been no live backend
execution or actual stream-network playback verification.

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

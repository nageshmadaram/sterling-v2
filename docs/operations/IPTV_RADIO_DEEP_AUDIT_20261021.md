# Sterling Spectrum IPTV + Radio — Mobile and Desktop Deep Audit

**Date:** 2026-10-21 (Asia/Kolkata)  
**Branches:** `main` and `dev` (sync required after this document commit)  
**Focus:** KITE/TV/Radio host navigation, IPTV player (embedded and standalone), Radio player, all responsive layouts, startup/hydration, security, rendering, stream lifecycle, fallback, search, player gestures and performance.

## Verification standard

The connected GitHub repository was inspected and changed directly. The assistant executed syntax parsing for **all inline JavaScript blocks** in `frontend/public/tv/index.html`, `sterling-iptv/sterling-iptv.html`, `frontend/public/radio/index.html`, and standalone `frontend/tv-mobile/mobile.js`; all parsed. The published and standalone IPTV files were also checked to contain the canonical `frontend/tv-mobile/mobile.js` and `mobile.css` text exactly. A focused V8 test executed the TV HTML/JavaScript string-escaping helpers against five adversarial input values. These were static/in-memory checks—not real playback, a TypeScript build, Vitest, or device QA.

**CI blocker:** On 2026-10-21, GitHub Actions `main` run `39545293266` and the `dev` runs both report failures across frontend typecheck, Vitest, mobile, backend, and Docker smoke jobs with **zero recorded executable job steps**. The logs endpoint returned `BlobNotFound`. This pattern predates the media changes. The actual test outcomes cannot be inferred. Fix runner availability or gather direct host test output.

## Artifact-by-artifact review

| Artifact | Areas reviewed | Source-level disposition | Remaining proof |
|---|---|---|---|
| `frontend/src/pages/SimpleTerminal.tsx` | KITE home/return navigation, mobile search/hamburger, TV/Radio header and Back routing | Home/close integration and media player-state routing changed in PR #248 | Mobile touch and configured Home behavior on real device |
| `frontend/src/styles/globals.css` | Header, market ticker, mobile command row, responsive media host | Rules suppressing enabled ticker removed; header and mobile controls restored | Screenshot comparisons at 320/390/768/desktop widths |
| `frontend/src/components/tv/TvWorkspace.tsx` | Iframe ready/hydrate, host commands, player-state and local preference bridge | Opaque-origin sandbox, exact frame sender check, key allowlist preserved | WebKit hydration and restore across reload |
| `frontend/src/components/radio/RadioWorkspace.tsx` | Iframe handshake, retry, host search/theme and state | Opaque-origin sandbox and media-only storage bridge preserved | Delayed loads/blocked CDN tests |
| `frontend/tv-mobile/mobile.js` | Spotify/YouTube mobile shell, mini/full player, search, queue, swipes, PiP | Search dismissal, bounded artwork, throttled PiP scroll and in-flow positioning | Physical Android/iOS gesture + PiP regression |
| `frontend/tv-mobile/mobile.css` | Responsive shell, landscape, fixed PiP, fullscreen | Canonical CSS matches both shipped IPTV copies | Browser layout matrix |
| `frontend/public/tv/index.html` | HLS/native playback, fullscreen, keyboard, mute, double tap, error fallback, source manager | Playlist context escaping, verified HTTPS artwork, stream-generation race guards, real fallback errors, user mute persistence, missing-source status | Network streams, fullscreen, permission, memory, media error recovery |
| `sterling-iptv/sterling-iptv.html` | Standalone parity with embedded IPTV | All mobile JS/CSS and playback, escaping, error-handling fixes synchronized | Standalone real-browser smoke |
| `frontend/public/radio/index.html` | Search, AM/SW tuner, modal, EQ, saved startup, metadata, cover, HLS, audio/pause | Zero-volume preservation, stale request cancellation, remote metadata hardening, artwork refresh/fallback/lock, late hydration, stream-generation guards, mobile HLS buffer limits, background retry backoff | Real station streaming, pause/mute/route and device battery |
| `frontend/tests/mediaRuntime.test.ts` | JSDOM executable media document smoke tests | Existing coverage for TV mobile search and Back; Radio search, modal, tuner and minimization | Run locally; CI did not execute |
| `frontend/tests/tvIntegrationSecurity.test.ts` | Static media contract, iframe isolation, source parity, native HLS and UI guarantees | Assertions extended for encoding, volume, search invalidation and stream revision | Run Vitest locally; CI did not execute |

## Confirmed code gaps addressed

1. **IPTV stale HLS events:** asynchronous manifest, fragment, error and fallback callbacks from old channel sessions could alter current playback, especially after rapid switching. Added per-playback generation guards to embedded and standalone player.
2. **IPTV incorrect substitute video:** a channel without backup could silently play an unrelated verified stream under the wrong title. Removed unrelated fallback; now reports unavailable.
3. **IPTV fatal backup freeze:** backup-HLS fatal errors lacked actionable state. Added visible prompt, HLS cleanup and retry support.
4. **IPTV unsafe playlist interpolation:** a general-purpose apostrophe backslash escape was used in HTML and event-handler contexts. Split HTML-attribute escaping and JSON-encoded JavaScript arguments, verified adversarial strings, and restricted remote poster URL handling.
5. **IPTV mute regression:** audio interaction and user mute preference were conflated. Resume now retains chosen mute state; unsolicited autoplay still starts muted.
6. **IPTV PiP scroll oscillation / CPU:** fixed-position video was previously a moving observer target. Compare stable in-flow wrapper against scroll viewport and schedule scroll updates by requestAnimationFrame.
7. **IPTV repeated DOM work:** hidden mobile home shelves rebuilt on search. Avoid re-render unless the home view is visible.
8. **IPTV stuck search after playing:** desktop, channel tiles and embedded mobile search paths now clear their search query after selection.
9. **Radio unexpected volume:** 0 volume was interpreted as a falsy missing setting, then raised to 10% by DSP. Preserve genuine zero across EQ changes.
10. **Radio stale remote search:** closing search did not invalidate pending network results. Abort and increment request revision before resetting browse state; synchronize desktop/mobile search inputs.
11. **Radio untrusted network metadata:** validate remote JSON station name/HTTPS stream/favicon, bound fields, escape text inserted into markup and reject malformed image URL attributes.
12. **Radio artwork and background cost:** reduced fallback raster to 384x384, bounded caches, added remote favicon failure fallback and manual refresh, locked concurrent artwork resolution, avoided repeated iTunes lookups for the same song.
13. **Radio stale playback and battery use:** ignore delayed HLS/Promise callbacks from previous station, throttle background play retries and reduce visualizer frequency.
14. **Radio late hydration:** apply saved settings if standalone fallback boot happens before the host preferences arrive; avoid duplicate initialization loops.
15. **Radio missing error notice:** exhausted backup stream attempts now explain unavailability rather than silently doing nothing; cap mobile HLS buffer length.

## Open / unverified issues (not marked passed)

| Priority | Gap or blocker | Evidence required to close |
|---|---|---|
| P0 | **CI jobs do not run (zero recorded steps)** | Healthy Actions runner with complete logs and passing typecheck/Vitest/mobile smoke |
| P0 | **No real device playback validation** | Android Chrome and iOS Safari portrait/landscape; double-tap fullscreen, escape/Back, touch interception, PiP and swipes |
| P1 | **Actual channel availability is external** | Verify HTTPS stream and native/Hls.js decoding, cross-origin behavior, geo blocks, 404 and DRM failures on multiple networks |
| P1 | **Standalone + embedded parity in real browsers** | Same playlist, selections, search persistence, player status and error responses on both URLs |
| P1 | **Radio live track artwork outside SomaFM** | Real station metadata endpoint/ICY metadata support; keep station emblem if track data unavailable, never invent music identity |
| P1 | **Host hydration/iframe startup races** | Emulate 0s/5s/15s hydration, offline, reload and repeated theme commands; check storage only media keys |
| P1 | **Streaming memory/CPU cannot be measured remotely** | Chrome Performance and Memory panels or equivalent; record long tasks, heap, video frame drops, HLS buffer size and battery |
| P1 | **Background audio varies by OS** | Explicit pause remains paused; play/pause through lock screen and media session; no autoplay storm |
| P2 | **Mobile layout and accessibility** | 320, 360, 390, 430, 768, 1024px and desktop; keyboard/VoiceOver/TalkBack, focus trap, reduced motion |
| P2 | **Actual CDN failures and network recovery** | Block external scripts/fonts/artwork, stall manifest/media, force network offline/online and verify retry without white screen |

## Mandatory acceptance commands on the authorized Sterling host

Use clean working tree; never overwrite local edits:

```bash
cd ~/Sterling
git status --short
git fetch origin --prune
git switch main
git pull --ff-only origin main
cd frontend
npm ci
npx tsc --noEmit
npx vitest run tests/mediaRuntime.test.ts tests/tvIntegrationSecurity.test.ts src/components/tv/__tests__/TvWorkspace.test.tsx src/components/radio/__tests__/RadioWorkspace.test.tsx
npm run build
```

The commands above are **not claimed executed**. On the actual host, examine console errors and the cloud tunnel's served asset version; update the running app only after validation.

## Acceptance boundaries

- No Kite credentials or broker APIs cross the media iframes. Continue to enforce `sandbox` with no `allow-same-origin`; only media allowlist storage keys may hydrate.
- Changes here do **not** enable live trading, waive release risk, prove broker order protections, or satisfy financial release certification.
- A GitHub merge and a current Cloudflare/ngrok/local deployment are different states. Do not claim mobile is updated without observing the running frontend revision.
- Status: **Source changes merged; JavaScript parse/static checks passed; application-wide runtime/performance/device certification remains BLOCKED.**

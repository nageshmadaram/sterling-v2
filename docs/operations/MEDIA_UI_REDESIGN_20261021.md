# Spectrum TV and Radio — interaction repair & coherent design (21 October 2026)

## Scope
Restructure embedded Spectrum IPTV and Radio across mobile and desktop around a **single top-level Sterling media header**: search icon → appearance/settings icon → operator profile. Keep audio and video transport distinct while using a shared theme menu, spacing, modal behavior, search conventions, and responsive shell.

## Correctness repairs
- Account profile is a portal outside the app's temporarily inert tree. A full viewport interaction shield blocks background iframe touches; the close button, backdrop click, Escape, mobile Back and photo cropper closing are supported. Document scrolling is locked while open and restored afterward.
- The mobile "app" stylesheet previously hid direct header buttons and fallback profile initials; explicit media-scoped overrides keep the avatar visible and tappable.
- One reusable React `MediaHeaderControls` replaces the large header theme dropdown, always-visible input, duplicated theme buttons and the top-level `...` media control. Search expands on demand; appearance choices and the existing player tools are in one popover. Search responds to child iframe requests and clears after playing.
- The media destination selector is scrollable in narrow headers without sacrificing profile/search hit targets.
- IPTV search results hide the All/Live/context/category ribbons while an actual query is present. The canonical mobile JS and CSS are copied exactly into both the served and standalone IPTV HTML documents.
- IPTV Netflix mobile mode reuses the established single video element and native HLS transport, with a separate cinematic collection skin and red navigation accents.
- Radio includes Spotify-style and YouTube Music-style presentation plus a separate Netflix-inspired cinematic audio skin. The SW/AM tuner button moved to Home, not the full-screen player.
- Radio full player surfaces (desktop and mobile) have explicitly opaque, station-hued backgrounds rather than translucent fixed palettes. Colors also change when the station reports a new track.
- Radio looks up station cover art from Radio Browser using an exact station-name match, HTTPS image validation, bounded caches, and a fallback graphic. Lookup errors do not block playback.
- On-air song information from an authoritative station feed (currently SomaFM) is displayed immediately. An album-art lookup is only accepted for an exact title/artist match. Missing track data is not guessed.

## Important Shazam-like recognition limitation
True identification from arbitrary live audio requires fingerprint capture, an approved audio recognition provider, matching catalogue, licensing/privacy review and API credentials. **No audio-fingerprint recognition service is connected** in this change. The player offers station metadata when available and explains this limitation otherwise. An iTunes title search is NOT audio recognition.

## Acceptance needed before release
1. `cd frontend && npm run build`
2. `npx vitest run tests/mediaRuntime.test.ts tests/tvIntegrationSecurity.test.ts src/pages/__tests__/SimpleTerminal.tv.test.tsx src/components/media/__tests__/MediaHeaderControls.test.tsx src/components/kite/__tests__/ProfileMenu.media.test.tsx src/components/radio/__tests__/RadioWorkspace.test.tsx src/components/tv/__tests__/MobileThemes.test.ts src/components/tv/__tests__/TvWorkspace.test.tsx`
3. Device QA matrix: 320/360/390/430px phone, tablet portrait/landscape, desktop 1280/1920px, Android Chrome and iOS Safari/WebKit. Test profile open/close and taps behind modal, theme/search popover interaction, TV active-channel playback across skins and fullscreen, Radio player miniplayer/back, tuner only on home, live streams, metadata fallback, stale network requests and background player continuity.
4. Inspect Chrome Performance long tasks, image memory and HLS buffers under low bandwidth and channel changes.
5. Compare local and Cloudflare served JS/CSS hash. GitHub branch update alone does not deploy to the user's host.
6. Keep the opaque-origin iframe boundaries. Do not alter Kite broker/trading execution.

## Release state
This is a source-level redesign on `dev`; successful production build, React/Vitest suites, real device rendering and public deployment are **not demonstrated** by GitHub source edits or JavaScript syntax validation alone.

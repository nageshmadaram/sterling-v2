# Radio / IPTV implementation audit — 21 October 2026

Scope: canonical embedded players, React sandbox bridges, shared header, mobile adapter, generated styles, standalone exports, authenticated preference API and tests.

## Findings and changes

| Finding | Resolution | Artifacts |
|---|---|---|
| Media preferences were saved only in browser storage and excluded from durable sync | Exact media allowlists on client/server; existing-owner migration; per-user outbox and server hydration reused | appearance/preferences, preferenceKeys, ui_preferences |
| Defaults were hard to discover | Direct Default channel / Default station action in shared appearance menu | MediaHeaderControls, TV/Radio message handlers |
| Remote Radio default depended on last-played/history snapshots | Independent startup snapshot, included in picker and station lookup | canonical Radio |
| Autoplay mute leaked into saved user intent and resume | Separate preferred mute from temporary policy mute; retry honors preference; stale async callbacks guarded | canonical TV, mobile adapter |
| TV punctuation and accents broke matches; normalization repeated per keystroke | Unicode-normalized multiword search and WeakMap index | canonical TV |
| Radio searched a whole phrase against individual fields | Token matching across title, subtitle, category and frequency; title-prefix ranking | canonical Radio |
| Remote Radio search gave no pending feedback | Accessible busy state; reset on clear, completion and cancellation | Radio, shared CSS |
| Unrelated discovery shelves cluttered search | Hide discovery shelves while search results are active | shared CSS |
| Mobile controls and modal styling differed | Shared spacing, touch targets, bottom sheets, focus outlines, reduced motion and pinch zoom | media/experience.css, canonical players |
| Two adjacent Tuner controls crowded Radio | Single mobile tuner sheet entry retained; station-step hit targets enlarged | shared CSS |
| IPTV recommendations repeated a fixed starter pool | Rank channels using current/recent channel language/category, favorites and verification | mobile adapter |
| Standalone Radio drifted from served app | Added deterministic self-contained Radio exporter and regenerated both standalone artifacts | export_sterling_radio.py, embed_tv_mobile.py |

The iframe remains an opaque-origin sandbox. Only media keys cross the bridge; credentials are never passed. Settings persist through the existing authenticated API/database, mapped to the owner user (including simulation owner mapping). Browser autoplay policy may still require an explicit tap for sound. Standalone files outside Sterling retain their own storage behavior; authenticated server sync is supplied by the Sterling host.

## Verification

- Frontend: 36 focused tests passed across media runtime, both frame bridges, shared header and preference sync. Includes audible-resume regression, normalized search, direct defaults, migration and account isolation.
- Backend: 6 preference tests passed, including owner isolation, durable media defaults, playlist limits and rejection of unsupported keys.
- TypeScript and production build passed. Existing bundle-size warnings remain.
- Static integration suite: the stale class/handler assertions were updated to the current accessible Home control and configured-section lookup. Added an interaction test proving that the close control returns from TV to the configured KITE section. All 16 static integration checks now pass.
- Chrome at 390×844: Radio and Spotify, YouTube and Netflix IPTV layouts rendered with no horizontal document overflow. All four screenshots inspected.
- All inline script blocks in the two canonical pages and two standalone exports parsed successfully.
- No deployment performed. Actual broadcaster availability, device audio/codec behavior, OS background playback, and iOS/Android gesture behavior need physical-device validation. No measured performance or exact product-replica claim is made.

## Rebuild/export

```bash
npm --prefix frontend run media:styles
python3 scripts/embed_tv_mobile.py
python3 scripts/export_sterling_radio.py
```

The public/media directory is generated and ignored; edit frontend/media/experience.css. Existing untracked frontend/public/radio/sterling-radio.html was left untouched.

## Spotify reference follow-up

Implemented against the five supplied Spotify screenshots:

- Explicit **All** Radio tab owns quick access, start listening, recommendations, recents and category shelves. SW/AM, Akashvani, Telugu, Tamil, global and podcast tabs render only their own channel collections. Search and Library suppress home shelves.
- Both shared host search bars have a combobox with recent picks, live suggestions, stale-query rejection, keyboard navigation and direct playback selection. Standalone search inputs offer suggestions too. Radio refreshes its suggestions after network results arrive.
- Spotify quick-access tiles, square shelves, category browse cards, library rows and expanded-player proportions share a common visual rhythm. Category browse cards use rotated station artwork; offline station covers now have several distinct graphic families.
- Radio artwork enrichment follows visible cards, limits concurrency and refreshes images in place rather than rebuilding controls. Language catalogs retain station favicon metadata. Broadcast-reported track metadata drives album art where supported; no unrelated album image is substituted for a station.
- IPTV resolves channel logos using playlist IDs and public IPTV channel/logo metadata, with exact/unique identity matching, request coalescing, a refresh interval, timeout and offline fallback. Source documentation: https://github.com/iptv-org/api#logos
- SW/AM has an independent expanded receiver window with frequency display, tuning slider, presets and shared playback controls. It reuses the single Radio audio engine and supports host Back, Escape and keyboard focus containment.

Validation at this stage: 53 frontend tests passed across seven suites, including new category, suggestion-selection and artwork tests; production build passed. Chrome confirms category shelf suppression and Radio layouts at 320, 390 and 430 px. Broadcaster availability and native device playback remain external validation items.

Final browser verification: both TV and Radio fit 320/390/430 px widths without horizontal document overflow. Six visible TV quick-access images loaded successfully while remote logos remained pending. Generic Radio Now Playing fills the 390×844 viewport; SW/AM opens its own receiver window. Clean-page browser flows completed with zero page errors. Remote logo promotion waits for image loading, and Radio station updates preserve a currently resolved track album image.

## Continuation: offline recovery and repeatable browser acceptance

- Artwork metadata now retries promptly when an offline startup comes online, aborts sibling fetches on failure, and rejects credential-bearing image URLs.
- SW/AM Next/Previous derives its starting frequency from the active station rather than a stale collapsed dial. Browser coverage verifies 738 → 820 kHz after selecting the first preset.
- Added `npm --prefix frontend run e2e:media`: a dedicated static preview and four Playwright tests, isolated from login and trading setup. Covers Radio collection visibility, keyboard suggestion selection, IPTV home/category separation, 320/390/430/768 px layouts, controlled WAV decoding with mute off, and explicit pause surviving the retry interval.
- Browser result: **4 passed** in installed Chrome. Broadcaster/network availability and physical iOS/Android behavior remain outside this controlled-browser result.

Continuation acceptance: **71 frontend tests passed** across eight suites, including all 16 static integration checks and the previously failing Home check. **4 isolated Chrome browser tests passed**. The production build passed with the existing bundle-size warning. Embedded and standalone artifacts were regenerated from the canonical sources.

The real Node gateway isolation/CSP regression test also passed. The dedicated browser suite starts and stops its own static server; no authenticated user settings, broker fixtures, or external broadcasts are needed.

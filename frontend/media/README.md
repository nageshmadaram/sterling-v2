# Sterling media interfaces

TV and Radio stay inside opaque-origin iframes. Their styles, icons and Inter /
Roboto fonts and the HLS player are built locally. External catalogs and broadcasts
remain network resources; interface initialization does not wait for a CDN.

- `public/tv/appearance.css`: TV theme surfaces, portrait and landscape layouts.
- `public/radio/appearance.css`: Spotify / YouTube Music Radio surfaces.
- `tv-mobile/mobile.js` and `mobile.css`: shared TV adapter sources.
- `src/pages/SimpleTerminal.tsx`: shared host header and search controls.
- `scripts/build-media-styles.mjs`: deterministic Tailwind and embedded-font assets.

`npm run dev`, `npm run test`, and `npm run build` compile media styles first.
The generated `public/media` directory is ignored and regenerated for each build.

After editing the shared TV adapter, run from the repository root:

```
npm --prefix frontend run media:styles
python3 scripts/embed_tv_mobile.py
```

This also regenerates `sterling-iptv/sterling-iptv.html` as a standalone document
with styles and fonts embedded. Do not independently edit that exported copy.

Validation:

```
npm --prefix frontend test -- --run tests/mediaRuntime.test.ts tests/tvIntegrationSecurity.test.ts src/components/radio/__tests__/RadioWorkspace.test.tsx src/pages/__tests__/SimpleTerminal.tv.test.tsx
npm --prefix frontend run build
node --test scripts/tests/tv_gateway.test.mjs
```

Native autoplay, background picture-in-picture, and stream availability still
depend on the browser, OS permissions and the broadcaster. The player must keep
an explicit play control and show useful failure/retry states.

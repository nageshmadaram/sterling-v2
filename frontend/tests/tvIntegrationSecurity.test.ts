import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// This Node-based security inspection stays outside src/, because the
// browser-only frontend tsconfig intentionally excludes Node globals.
// Vitest runs from the frontend package directory.
const tvPage = readFileSync(resolve(process.cwd(), 'public/tv/index.html'), 'utf8');
const nginx = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8');
const gateway = readFileSync(resolve(process.cwd(), '../scripts/serve_frontend.mjs'), 'utf8');
const latestTv = readFileSync(resolve(process.cwd(), '../sterling-iptv/sterling-iptv.html'), 'utf8');
const originalKiteCss = readFileSync(resolve(process.cwd(), 'src/styles/terminal.css'), 'utf8');
const spectrumSpacingCss = readFileSync(resolve(process.cwd(), 'src/styles/globals.css'), 'utf8');

describe('Sterling TV static integration safety', () => {

  it('keeps user-configured KITE home, mobile search and ticker visible',()=>{
    const page=readFileSync(resolve(process.cwd(),'src/pages/SimpleTerminal.tsx'),'utf8');
    expect(page).toContain('className="sterling-home-button"');
    expect(page).toContain('className="sterling-return-home"');
    expect(page).toContain("handleKiteNav(preferredSection || 'dashboard')");
    expect(page).toContain('className="sterling-mobile-command-row"');
    expect(page).toContain('id="sterling-mobile-header-actions"');
    expect(spectrumSpacingCss).not.toContain('.workspace-market-strip:not([data-page="dashboard"]) { display:none; }');
    expect(spectrumSpacingCss).not.toContain('.mobile-pane-host .kite-ticker-strip { display:none; }');
  });

  it('does not splice untrusted playlists into executable inline handlers',()=>{
    for(const html of [tvPage,latestTv]) {
      expect(html).toContain('function escapeJsAttr(str)');
      expect(html).toContain('return escapeHtml(JSON.stringify(String(str ?? \'\')));');
      expect(html).not.toContain("'\${escapeAttr(");
      expect(html).toContain("playbackGeneration===generation");
      expect(html).toContain("url.protocol==='https:'");
    }
  });

  it('preserves Radio zero volume and rejects stale remote search results',()=>{
    const radio=readFileSync(resolve(process.cwd(),'public/radio/index.html'),'utf8');
    expect(radio).toContain('const requested=Number.isFinite(appConfig.volume)?appConfig.volume:0.85;');
    expect(radio).toContain('searchRevision++;searchAbort?.abort();searchAbort=null;');
    expect(radio).toContain("typeof s.url_resolved==='string'");
    expect(radio).toContain('artworkLookupInFlight');
    expect(radio).toContain('playbackRevision===revision');
    expect(radio).toContain('const generation=playbackRevision;');
    expect(radio).toContain('if(generation!==playbackRevision||!activeStream');
    expect(radio).toContain('syncPlayerUI(stationEmblem,!overrideUrl)');
    expect(radio).toContain('if (resetStationMetadata || !isPlaying)');
    expect(radio).toContain('stationArtworkInflight<2');
    expect(radio).toContain('catalogItems.slice(0,16)');
    expect(radio).toContain('scheduleStationArtworkPaint()');
    expect(radio).toContain('const artist=norm(item.artistName)');
    expect(radio).toContain('if (radioBooted) {');
  });

  it('guards mobile IPTV fullscreen, search dismissal, user audio choice and queue',()=>{
    expect(tvPage).toContain('lastFullscreenGestureAt');
    expect(tvPage).toContain('if (!userHasInteractedWithAudio) video.muted = true;');
    expect(tvPage).not.toContain('video.muted = !userHasInteractedWithAudio');
    expect(tvPage).toContain('function clearSelectionSearch()');
    expect(tvPage).toContain('function setScrollPip(value)');
    expect(tvPage).toContain('if(Boolean(value)===wasFloating)return;');
    expect(tvPage).toContain("document.getElementById('mobile-sticky-player-wrap')");
    expect(tvPage).toContain("view==='home')renderHome()");
    expect(tvPage).toContain("if(searchInputEl.value.trim())clearSelectionSearch()");
    expect(tvPage).toContain("'backdrop','Player background: black / auto'");
    expect(tvPage).toContain("installPlayerSwipes(now,dir=>");
    expect(tvPage).toContain("installPlayerSwipes(mini,dir=>");
  });

  it('isolates Radio dialogs and restores mobile tuner, search and album identity art',()=>{
    const radio=readFileSync(resolve(process.cwd(),'public/radio/index.html'),'utf8');
    expect(radio).toContain('function openMobileTuner()');
    expect(radio).toContain('function syncRadioModalState()');
    expect(radio).toContain('radio-modal-open');
    expect(radio).toContain('aria-label="Open radio tuner"');
    expect(radio).toContain('function closeMobileSheet()');
    expect(radio).toContain('clearAllSearches();');
    expect(radio).toContain('getStationIdentityThumbnail');
    expect(radio).toContain("stationBadgeCache[keys[0]]");
    expect(radio).toContain('RADIO_MIRRORS');
  });
  it('ships local media styles, fonts and icons without runtime Tailwind',()=>{
    const radio=readFileSync(resolve(process.cwd(),'public/radio/index.html'),'utf8');
    for(const page of [tvPage,radio]) {
      expect(page).toContain('href="/media/interface.css"');
      expect(page).toContain('href="/media/fonts.css"');
      expect(page).not.toContain('src="https://cdn.tailwindcss.com"');
    }
    expect(radio).toContain('href="/media/icons.css"');
    expect(gateway).toContain("style-src 'self' 'unsafe-inline'");
    expect(readFileSync(resolve(process.cwd(),'public/media/interface.css'),'utf8')).toContain('.flex');
    expect(readFileSync(resolve(process.cwd(),'public/media/icons.css'),'utf8')).toContain('data:font/woff2;base64,');
  });
  it('embeds the same mobile UI assets in the standalone and published players', () => {
    for (const file of ['mobile.css','mobile.js']) {
      const asset = readFileSync(resolve(process.cwd(), 'tv-mobile', file), 'utf8');
      expect(tvPage).toContain(asset);
      expect(latestTv).toContain(asset);
    }
  });

  it('ships the current IPTV design and controls rather than a stale player copy', () => {
    const title = (html: string) => html.match(/<title>(.*?)<\/title>/)?.[1];
    expect(title(tvPage)).toBe(title(latestTv));
    expect(title(tvPage)).toBe('Sterling TV');
    for (const id of ['audio-eq-popover', 'pip-drag-header', 'pip-resize-handle']) {
      expect(latestTv).toContain(`id="${id}"`);
      expect(tvPage).toContain(`id="${id}"`);
    }
  });
  it('serves a distinct streaming document instead of injecting its scripts into trading', () => {
    expect(tvPage).toContain('id="video-player"');
    expect(tvPage).toContain('id="source-manager-modal"');
    expect(tvPage).toContain('id="tv-tools-reveal"');
    expect(tvPage).toContain("type:'sterling.tv.ready'");
    expect(tvPage).toContain('window.top === window.self');
    expect(tvPage).not.toContain('localStorage.getItem(');
    expect(tvPage).not.toContain('localStorage.setItem(');
  });

  it('includes newly pushed v14 IPTV channels, startup settings and mobile tools', () => {
    expect(tvPage).toContain('streamos_startup_channel_v14');
    expect(tvPage).toContain('streamos_sources_v14');
    expect(tvPage).toContain('playStream(getStartupChannelObject(),0)');
    expect(tvPage).toContain('toggleEmbeddedTvTools');
    expect(tvPage).toContain('toggleHoverPreviewAudio');
    expect(tvPage).toContain("type:'sterling.tv.ready'");
  });

  it('spaces adjacent KITE and TV underlines without restyling original KITE', () => {
    expect(spectrumSpacingCss).toContain('margin-inline-start: 10px');
    expect(spectrumSpacingCss).toContain('.mobile-view-toggle.unified-view-toggle');
    expect(originalKiteCss).not.toContain('sterling-spectrum-tabs');
  });

  it('keeps original KITE green/amber underline and scopes active-TV underline', () => {
    expect(spectrumSpacingCss).toContain('border-bottom:2px solid var(--k-green)');
    expect(spectrumSpacingCss).toContain('[aria-checked=false]{color:var(--k-warn);border-bottom-color:var(--k-amber)}');
    expect(spectrumSpacingCss).toContain('[aria-current="page"]');
    expect(spectrumSpacingCss).toContain('border-bottom-color: transparent');
    expect(spectrumSpacingCss).toContain('data-testid="spectrum-simulation-tab"');
    expect(originalKiteCss).not.toContain('sterling-spectrum-tabs');
  });

  it('supports mobile native HLS, blocked autoplay retry and reliable hydration', () => {
    expect(tvPage).toContain('id="tv-mobile-play"');
    expect(tvPage).toContain('function resumeTvPlayback()');
    expect(tvPage).toContain("typeof Hls !== 'undefined'");
    expect(tvPage).toContain('function startNativeHlsPlayback(');
    expect(tvPage).toContain('preferNativeHls(video)');
    expect(tvPage).toContain('showTvPlaybackPrompt(');
    expect(tvPage).toContain('const readyTimer=setInterval(');
    expect(tvPage).toContain('clearInterval(readyTimer)');
    expect(tvPage).not.toContain('PROXY_RELAYS');
  });

  it('never automatically sends the operator playlist URL to public proxy services', () => {
    expect(tvPage).not.toContain('corsproxy.io');
    expect(tvPage).not.toContain('api.allorigins.win');
    expect(tvPage).not.toContain('PROXY_RELAYS');
    expect(tvPage).not.toContain('98% Match');
    expect(tvPage).toContain('Guide preview');
  });

  it('secures the actual Node release gateway, not only optional Nginx deployments', () => {
    expect(gateway).toContain("decoded === '/tv/index.html'");
    expect(gateway).toContain('sandbox allow-scripts allow-forms allow-downloads allow-presentation');
    expect(gateway).not.toContain('sandbox allow-scripts allow-same-origin');
    expect(gateway).toContain('media-src https: http: data: blob:');
    expect(gateway).toContain('frame-ancestors \'self\'');
    expect(gateway).toContain("isTvDocument ? tvCsp : isRadioDocument ? radioCsp : tradingCsp");
  });

  it('enforces an opaque-origin media document even when navigated to directly', () => {
    expect(nginx).toContain('location = /tv/index.html');
    expect(nginx).toContain("sandbox allow-scripts allow-forms allow-downloads allow-presentation");
    expect(nginx).not.toContain('sandbox allow-scripts allow-same-origin');
    expect(nginx).toContain("frame-ancestors 'self'");
    expect(nginx).toContain("worker-src blob:");
    expect(nginx).toContain('location = /tv/');
  });
});

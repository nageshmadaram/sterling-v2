import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// This Node-based security inspection stays outside src/, because the
// browser-only frontend tsconfig intentionally excludes Node globals.
// Vitest runs from the frontend package directory.
const tvPage = readFileSync(resolve(process.cwd(), 'public/tv/index.html'), 'utf8');
const nginx = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8');
const gateway = readFileSync(resolve(process.cwd(), '../scripts/serve_frontend.mjs'), 'utf8');
const originalKiteCss = readFileSync(resolve(process.cwd(), 'src/styles/terminal.css'), 'utf8');
const spectrumSpacingCss = readFileSync(resolve(process.cwd(), 'src/styles/globals.css'), 'utf8');

describe('Sterling TV static integration safety', () => {
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
    expect(gateway).toContain("isTvDocument ? tvCsp : tradingCsp");
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

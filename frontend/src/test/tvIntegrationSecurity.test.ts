import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const tvPage = readFileSync(new URL('../../public/tv/index.html', import.meta.url), 'utf8');
const nginx = readFileSync(new URL('../../nginx.conf', import.meta.url), 'utf8');

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

  it('never automatically sends the operator playlist URL to public proxy services', () => {
    expect(tvPage).not.toContain('corsproxy.io');
    expect(tvPage).not.toContain('api.allorigins.win');
    expect(tvPage).not.toContain('PROXY_RELAYS');
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

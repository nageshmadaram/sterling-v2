import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(resolve(root, 'index.html'), 'utf8');
const nginx = readFileSync(resolve(root, 'nginx.conf'), 'utf8');

describe('nginx Content-Security-Policy', () => {
  it('allows every inline bootstrap script by exact hash', () => {
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    expect(inline.length).toBeGreaterThan(0);
    const hashes = inline.map(script => `sha256-${createHash('sha256').update(script, 'utf8').digest('base64')}`);
    const allowed = [...nginx.matchAll(/'sha256-([^']+)'/g)].map(match => `sha256-${match[1]}`);
    expect(allowed.sort()).toEqual(hashes.sort());
    expect(nginx).toContain('https://telegram.org/js/telegram-web-app.js');
  });

  it('never falls back to unsafe-inline / unsafe-eval for scripts', () => {
    const scriptSrc = /script-src([^;"]*)/.exec(nginx)?.[1] ?? '';
    expect(scriptSrc).not.toContain('unsafe-inline');
    expect(scriptSrc).not.toContain('unsafe-eval');
  });

  it('allows Telegram framing and forbids plugins', () => {
    expect(nginx).toContain("frame-ancestors 'self' https://*.telegram.org https://telegram.org https://t.me");
    expect(nginx).not.toContain('X-Frame-Options "DENY"');
    expect(nginx).toContain("object-src 'none'");
  });

  it('proxies the live websocket with upgrade headers', () => {
    expect(nginx).toMatch(/location \/api\/v1\/stream\/[\s\S]*proxy_set_header Upgrade \$http_upgrade/);
  });
});


it('routes the Mini App and Sterling login form to the backend', () => {
  expect(nginx).toContain('location ~ ^/tma(?:/|$)');
  expect(nginx).toContain('proxy_set_header X-User-Id "";');
});

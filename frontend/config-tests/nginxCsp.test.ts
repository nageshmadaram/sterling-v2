import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(resolve(root, 'index.html'), 'utf8');
const nginx = readFileSync(resolve(root, 'nginx.conf'), 'utf8');

describe('nginx Content-Security-Policy', () => {
  it('allows exactly the inline bootstrap script in index.html, by hash', () => {
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    expect(inline).toHaveLength(1);
    const hash = `sha256-${createHash('sha256').update(inline[0], 'utf8').digest('base64')}`;
    // If this fails, index.html's inline script changed: update the hash in
    // nginx.conf (script-src) or the production UI will not run its theme bootstrap.
    expect(nginx).toContain(`'${hash}'`);
  });

  it('never falls back to unsafe-inline / unsafe-eval for scripts', () => {
    const scriptSrc = /script-src([^;"]*)/.exec(nginx)?.[1] ?? '';
    expect(scriptSrc).not.toContain('unsafe-inline');
    expect(scriptSrc).not.toContain('unsafe-eval');
  });

  it('forbids framing and plugins', () => {
    expect(nginx).toContain("frame-ancestors 'none'");
    expect(nginx).toContain("object-src 'none'");
  });

  it('proxies the live websocket with upgrade headers', () => {
    expect(nginx).toMatch(/location \/api\/v1\/stream\/[\s\S]*proxy_set_header Upgrade \$http_upgrade/);
  });
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const serverScript = path.join(root, 'scripts/serve_frontend.mjs');

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function awaitReady(origin, process, limitMs = 8000) {
  const start = Date.now();
  while (Date.now() - start < limitMs) {
    if (process.exitCode !== null) throw Error('isolated gateway quit before ready');
    try {
      const res = await fetch(origin + '/__sterling_web_health', {
        signal: AbortSignal.timeout(350),
      });
      if (res.ok) return;
    } catch { /* isolated test gateway not yet bound */ }
    await new Promise(resolve => setTimeout(resolve, 70));
  }
  throw Error('isolated gateway did not become ready');
}

test('real Node gateway retires unguarded player documents and preserves the SPA CSP', { timeout: 15000 }, async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), 'sterling-tv-gateway-'));
  let child;
  try {
    await mkdir(path.join(temporary, 'tv'));
    await mkdir(path.join(temporary, 'radio'));
    await writeFile(path.join(temporary, 'index.html'), '<html><body>TRADING APP</body></html>');
    await writeFile(path.join(temporary, 'tv/index.html'), '<html><body>TV APP<script>void 0;</script></body></html>');
    await writeFile(path.join(temporary, 'radio/index.html'), '<html><body>RADIO APP<script>void 0;</script></body></html>');
    const port = await freePort();
    const origin = 'http://127.0.0.1:' + port;
    child = spawn(process.execPath, [serverScript], {
      cwd: root,
      env: {
        ...process.env,
        STERLING_STATIC_ROOT: temporary,
        STERLING_WEB_PORT: String(port),
        STERLING_CALLBACK_PORT: '0',
        STERLING_RELEASE_MANIFEST: path.join(temporary, 'missing-manifest.json'),
      },
      stdio: 'ignore',
    });
    await awaitReady(origin, child);
    for (const media of ['tv', 'radio']) {
      const redirect = await fetch(origin + '/' + media + '/', { redirect: 'manual' });
      assert.equal(redirect.status, 302);
      assert.equal(redirect.headers.get('location'), '/' + media);
      const launcher = await fetch(origin + '/' + media);
      assert.equal(launcher.status, 200);
      assert.match(await launcher.text(), /TRADING APP/);
      for (const document of ['/' + media + '/index.html', '/retained/release/' + media + '/index.html', '/' + media + '/%69ndex.html']) {
        for (const method of ['GET', 'HEAD']) {
          const denied = await fetch(origin + document, { method });
          assert.equal(denied.status, 410, method + ' ' + document);
          assert.equal(denied.headers.get('cache-control'), 'no-store');
        }
      }
    }
    const trade = await fetch(origin + '/');
    assert.equal(trade.status, 200);
    assert.equal(await trade.text(), '<html><body>TRADING APP</body></html>');
    assert.match(trade.headers.get('content-security-policy') || '', /img-src 'self' data: blob:/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /sandbox allow-scripts/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /script-src-attr 'unsafe-inline'/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /media-src https:/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /script-src-attr 'unsafe-inline'/);
  } finally {
    if (child && child.exitCode === null) {
      child.kill('SIGTERM');
      await Promise.race([
        new Promise(resolve => child.once('exit', resolve)),
        new Promise(resolve => setTimeout(resolve, 2000)),
      ]);
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
    await rm(temporary, { recursive: true, force: true });
  }
});

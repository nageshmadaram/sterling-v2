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

test('real Node gateway isolates TV document on direct URLs and preserves trading CSP', { timeout: 15000 }, async () => {
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
    for (const route of ['/tv', '/tv/']) {
      const redirect = await fetch(origin + route, { redirect: 'manual' });
      assert.equal(redirect.status, 302);
      assert.equal(redirect.headers.get('location'), '/tv/index.html');
    }
    const tv = await fetch(origin + '/tv/index.html');
    assert.equal(tv.status, 200);
    assert.match(await tv.text(), /nonce="[a-zA-Z0-9+/=]+"/);
    assert.equal(tv.headers.get('cache-control'), 'no-store');
    const csp = tv.headers.get('content-security-policy') || '';
    assert.match(csp, /sandbox allow-scripts allow-forms allow-downloads allow-presentation/);
    assert.match(csp, /script-src-attr 'unsafe-inline'/);
    assert.doesNotMatch(csp, /allow-same-origin/);
    assert.match(csp, /media-src https: http: data: blob:/);
    assert.match(csp, /worker-src blob:/);
    assert.match(csp, /frame-ancestors 'self'/);
    const head = await fetch(origin + '/tv/index.html', { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(head.headers.get('cache-control'), 'no-store');
    const headCsp = head.headers.get('content-security-policy') || '';
    assert.match(headCsp, /sandbox allow-scripts allow-forms/);
    assert.doesNotMatch(headCsp, /allow-same-origin/);
    assert.match(headCsp, /media-src https: http: data: blob:/);
    for (const route of ['/radio', '/radio/']) {
      const redirect = await fetch(origin + route, {redirect:'manual'});
      assert.equal(redirect.status,302);
      assert.equal(redirect.headers.get('location'),'/radio/index.html');
    }
    const radio = await fetch(origin + '/radio/index.html');
    assert.equal(radio.status,200);
    assert.match(await radio.text(), /RADIO APP<script nonce="[a-zA-Z0-9+/=]+"/);
    assert.equal(radio.headers.get('cache-control'),'no-store');
    const radioCsp=radio.headers.get('content-security-policy') || '';
    assert.match(radioCsp,/sandbox allow-scripts allow-forms allow-downloads allow-presentation/);
    assert.match(radioCsp,/script-src-attr 'unsafe-inline'/);
    assert.doesNotMatch(radioCsp,/allow-same-origin/);
    assert.match(radioCsp,/media-src https: http: data: blob:/);
    assert.match(radioCsp,/connect-src https: http:/);
    assert.match(radioCsp,/style-src 'unsafe-inline' https:\/\/fonts.googleapis.com https:\/\/cdnjs.cloudflare.com/);
    const radioHead=await fetch(origin+'/radio/index.html',{method:'HEAD'});
    assert.equal(radioHead.status,200);
    assert.match(radioHead.headers.get('content-security-policy') || '', /sandbox allow-scripts/);
    const trade = await fetch(origin + '/');
    assert.equal(trade.status, 200);
    assert.equal(await trade.text(), '<html><body>TRADING APP</body></html>');
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /sandbox allow-scripts/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /script-src-attr 'unsafe-inline'/);
    assert.doesNotMatch(trade.headers.get('content-security-policy') || '', /media-src https:/);
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

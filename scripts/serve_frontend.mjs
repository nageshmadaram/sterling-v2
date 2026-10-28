// Production assets and API/WebSocket proxy share one loopback origin.
import http from 'node:http';
import {randomBytes} from 'node:crypto';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// All public origins and localhost:5180 read this ONE atomic committed build.
 // The symlink target may change without restarting the Node gateway/tunnels.
const gatewayDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(process.env.STERLING_STATIC_ROOT ||
  path.join(gatewayDir, 'frontend/dist'));
const port = Number(process.env.STERLING_WEB_PORT || 5180);
const backend = new URL(process.env.STERLING_API_TARGET || 'http://127.0.0.1:8000');
if (!['127.0.0.1', 'localhost'].includes(backend.hostname) || backend.protocol !== 'http:') throw new Error('Backend must be loopback HTTP');
const releaseFile = process.env.STERLING_RELEASE_MANIFEST ||
  path.join(gatewayDir, '.runtime/stack/published.json');
function committedRelease() {
  try {
    const value = JSON.parse(fs.readFileSync(releaseFile, 'utf8'));
    return /^[a-f0-9]{40,64}$/.test(value.sha) ? value.sha : null;
  } catch { return null; }
}
const isApi = p => /^\/(api(?:\/|$)|tma(?:\/|$)|health(?:\/|$)|healthz(?:\/|$))/.test(p);
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2','.json':'application/json'};
// The supervisor owns this 0600 state file. The secondary ngrok tunnel
// reaches the gateway over local HTTP, but its PUBLIC hop is verified HTTPS.
// Never trust a caller-provided X-Forwarded-Proto header on its own.
const stackStatusFile = process.env.STERLING_STACK_STATUS_FILE ||
  path.join(gatewayDir, '.runtime/stack/status.json');
function isSupervisorNgrokHost(host) {
  if (typeof host !== 'string') return false;
  try {
    const state = JSON.parse(fs.readFileSync(stackStatusFile, 'utf8'));
    const candidate = new URL(state.ngrok_url || '');
    return candidate.protocol === 'https:' && candidate.host === host &&
      Number(state.children?.ngrok) > 1 && state.phase !== 'stopped';
  } catch { return false; }
}
function proxyHeaders(req) {
  const headers = {...req.headers};
  // Preserve the public host for the backend's remote-auth boundary; never
  // accept arbitrary caller-supplied forwarding identity or scheme.
  delete headers['x-user-id'];
  delete headers['x-sterling-client-ip'];
  delete headers['x-sterling-client-country'];
  delete headers['x-forwarded-for'];
  delete headers['x-forwarded-host'];
  delete headers['x-forwarded-proto'];
  headers['x-forwarded-host'] = req.headers.host;
  let publicCloudflare = false;
  try { const state = JSON.parse(fs.readFileSync(stackStatusFile,'utf8')); publicCloudflare = new URL(state.cloudflare_url || '').host === req.headers.host && Number(state.children?.cloudflare) > 1; } catch {}
  const candidate = publicCloudflare ? req.headers['cf-connecting-ip'] : isSupervisorNgrokHost(req.headers.host) ? String(req.headers['x-forwarded-for'] || '').split(',').at(-1)?.trim() : req.socket.remoteAddress;
  if (typeof candidate === 'string' && net.isIP(candidate)) headers['x-sterling-client-ip'] = candidate;
  const country = req.headers['cf-ipcountry'];
  if (publicCloudflare && typeof country === 'string' && /^[A-Z]{2}$/.test(country)) headers['x-sterling-client-country'] = country;
  headers['x-forwarded-proto'] =
    req.headers['cf-visitor']?.includes('https') || isSupervisorNgrokHost(req.headers.host)
      ? 'https' : 'http';
  return headers;
}
// Generic public shell contains no account data. Retain it during backend restarts
// so availability monitoring cannot confuse a broker/API outage with a dead tunnel.
const shellFile = process.env.STERLING_TMA_SHELL_CACHE || path.join(gatewayDir, '.runtime/stack/mini-app-shell.json');
function cachedDesk(res) {
  try {
    const shell = JSON.parse(fs.readFileSync(shellFile, 'utf8'));
    if (!shell.html.includes('Telegram Mini App')) return false;
    res.writeHead(200, { ...shell.headers, 'cache-control': 'no-store' });
    res.end(shell.html); return true;
  } catch { return false; }
}
const server = http.createServer((req,res) => {
  const pathname = new URL(req.url,'http://localhost').pathname;
  if (pathname === '/__sterling_web_health') {
    res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
    res.end(JSON.stringify({service:'sterling-web',ready:fs.existsSync(path.join(root,'index.html')),
      release_commit:committedRelease()})); return;
  }
  if (['/tma/','/api/v1/tma/'].includes(pathname)) { res.writeHead(302, {Location:pathname.slice(0,-1), 'Cache-Control':'no-store'}); res.end(); return; }
  if (isApi(pathname)) {
    const anonymous = /^\/api\/v1\/auth\/(login|refresh)$/.test(pathname) || pathname === '/api/v1/kite/callback' || ['/tma','/api/v1/tma','/tma/operator'].includes(pathname) || /^\/health/.test(pathname);
    const mediaDocument = /^\/api\/v1\/media\/(tv|radio)\/document$/.test(pathname) && ['GET','HEAD'].includes(req.method);
    const miniAuth = /^\/(api\/v1\/)?tma\//.test(pathname) && req.headers['x-telegram-init-data'];
    if (!anonymous && !miniAuth && !mediaDocument && !req.headers.authorization?.startsWith('Bearer ')) {
      if(req.method === 'GET' && req.headers.accept?.includes('text/html')) {res.writeHead(302,{Location:'/tma','Cache-Control':'no-store'});res.end();return;}
      res.writeHead(401,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end('{"detail":"Sign in to Sterling or open the desk from Telegram."}');return;
    }
    const deskShell = req.method === 'GET' && ['/tma', '/api/v1/tma'].includes(pathname);
    const upstream = http.request({hostname:backend.hostname,port:backend.port || 80,path:req.url,method:req.method,headers:proxyHeaders(req)}, response => {
      if (deskShell && response.statusCode >= 500 && cachedDesk(res)) { response.resume(); return; }
      if (deskShell && response.statusCode === 200) {
        const parts = []; let length = 0;
        response.on('data', part => { length += part.length; if (length <= 2_000_000) parts.push(part); });
        response.on('end', () => {
          if (length > 2_000_000) return;
          const html = Buffer.concat(parts).toString('utf8');
          if (!html.includes('Telegram Mini App')) return;
          const headers = Object.fromEntries(Object.entries(response.headers).filter(([key]) => ['content-type', 'content-security-policy', 'x-content-type-options', 'referrer-policy'].includes(key)));
          try { fs.writeFileSync(shellFile + '.tmp', JSON.stringify({html, headers}), { mode: 0o600 }); fs.renameSync(shellFile + '.tmp', shellFile); } catch {}
        });
      }
      res.writeHead(response.statusCode,response.headers); response.pipe(res);
    });
    upstream.on('error',()=>{ if (deskShell && !res.headersSent && cachedDesk(res)) return; if (!res.headersSent) res.writeHead(503,{'Content-Type':'application/json','Cache-Control':'no-store'}); res.end('{"detail":"Sterling backend is reconnecting. Try again shortly."}'); });
    req.on('aborted',()=>upstream.destroy()); req.pipe(upstream); return;
  }
  if (!['GET','HEAD'].includes(req.method)) {res.writeHead(405);res.end();return;}
  // All TV entry paths must resolve to the dedicated, opaque-origin
  // multimedia document. Never fall back to the trading SPA for /tv.
  if (['/tv/','/radio/'].includes(pathname)) {
    const media = pathname.startsWith('/radio') ? 'radio' : 'tv';
    res.writeHead(302, {'Location':'/'+media,'Cache-Control':'no-store',
      'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'});
    res.end(); return;
  }
  let decoded;
  try {decoded=decodeURIComponent(pathname);} catch {res.writeHead(400);res.end();return;}
  const target = path.resolve(root,'.'+decoded);
  if (!target.startsWith(root+path.sep) && target !== root) {res.writeHead(404);res.end();return;}
  const relative = path.relative(root, target).split(path.sep).join('/');
  if (/(?:^|\/)(?:tv|radio)\/.*\.html$/i.test(relative)) {
    res.writeHead(410, {'Cache-Control':'no-store','Content-Type':'text/plain'});
    res.end('Open this player from the Sterling launcher.'); return;
  }
  let file = target;
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
    if (path.extname(decoded) || (/^\/(docs|redoc|openapi)(\/|$)/.test(decoded) || /(?:^|\/)\./.test(decoded))) {res.writeHead(404);res.end();return;}
    file=path.join(root,'index.html');
  }
  if (!fs.existsSync(file)) {res.writeHead(503);res.end('Frontend build missing');return;}
  const nonce = randomBytes(18).toString('base64');
  const tradingCsp = `default-src 'self'; script-src 'self' 'nonce-${nonce}'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self' https://*.telegram.org https://telegram.org https://t.me; base-uri 'self'; form-action 'self'`;
  res.writeHead(200,{'Content-Type':mime[path.extname(file)] || 'application/octet-stream',
    'Cache-Control':file.endsWith('index.html')?'no-store':(decoded.startsWith('/media/') || /^\/(tv|radio)\/.*\.css$/.test(decoded))?'no-cache':'public, max-age=3600',
    'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
    'Content-Security-Policy':tradingCsp});
  if (req.method==='HEAD') {res.end();return;}
  if (file.endsWith('.html')) {res.end(fs.readFileSync(file,'utf8').replace(/<script\b/g,`<script nonce="${nonce}"`));return;}
  fs.createReadStream(file).on('error',()=>res.destroy()).pipe(res);
});
server.on('upgrade',(req,socket,head) => {
  if (!isApi(new URL(req.url,'http://localhost').pathname)) {socket.destroy();return;}
  const upstream=net.connect(Number(backend.port || 80),backend.hostname,()=>{
    const headers=proxyHeaders(req);
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${Object.entries(headers).map(([k,v])=>`${k}: ${v}`).join('\r\n')}\r\n\r\n`);
    if (head.length) upstream.write(head);
    upstream.pipe(socket);socket.pipe(upstream);
  });
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());socket.on('close',()=>upstream.destroy());
});
server.listen(port,'127.0.0.1',()=>console.log(`Sterling web ready on http://127.0.0.1:${port}`));
// Existing Kite developer apps often retain the former Vite callback port.
// Keep that loopback callback reachable without exposing another app/API origin.
const callbackPort = Number(process.env.STERLING_CALLBACK_PORT ?? 5173);
const callbackServers = [];
if (Number.isInteger(callbackPort) && callbackPort > 0 && callbackPort <= 65535 && callbackPort !== port) {
  for (const host of ['127.0.0.1', '::1']) {
    const callback = http.createServer((req, res) => {
      const incoming = new URL(req.url, 'http://localhost');
      const headers = {'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff'};
      if (req.method !== 'GET' || incoming.pathname !== '/api/v1/kite/callback') {
        res.writeHead(404, headers); res.end(); return;
      }
      res.writeHead(302, {...headers, Location:`http://127.0.0.1:${port}/api/v1/kite/callback${incoming.search}`});
      res.end();
    });
    callback.on('error', error => console.warn(`Kite callback listener ${host}:${callbackPort}: ${error.code}`));
    callback.listen(callbackPort, host);
    callbackServers.push(callback);
  }
}
for (const sig of ['SIGINT','SIGTERM']) process.on(sig,()=>{
  for (const callback of callbackServers) if (callback.listening) callback.close();
  server.close(()=>process.exit(0));
});

// Push only. Never cache authenticated APIs, broker sessions, or trade mutations.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

function safeDestination(raw) {
  // Notification links are generated server-side, but validate them again in
  // the service worker: only Sterling's own application entry may be opened.
  const root = new URL('./', self.registration.scope);
  try {
    const target = new URL(typeof raw === 'string' ? raw : './', root);
    if (target.origin !== root.origin || target.pathname !== root.pathname) return root.href;
    const allowed = new URLSearchParams();
    const category = target.searchParams.get('notification');
    if (['signals', 'pcr', 'alerts'].includes(category)) {
      allowed.set('view', 'live');
      allowed.set('notification', category);
      if (category === 'signals') {
        const engine = target.searchParams.get('strategy');
        if (['supertrend', 'snapback', 'gamma_move', 'ema_trend'].includes(engine))
          allowed.set('strategy', engine);
        const at = target.searchParams.get('at');
        if (at && /^\d{10,14}$/.test(at)) allowed.set('at', at);
        for (const key of ['signal', 'symbol']) {
          const text = target.searchParams.get(key);
          if (text && text.length <= (key === 'signal' ? 180 : 80)) allowed.set(key, text);
        }
      }
    }
    root.search = allowed.toString();
    return root.href;
  } catch {
    return root.href;
  }
}

self.addEventListener('push', event => {
  let data = {title: 'Sterling notification', body: 'New event', url: './'};
  try {
    const received = event.data?.json();
    if (received && typeof received === 'object') data = {...data, ...received};
  } catch { /* malformed payload keeps the safe fallback */ }
  event.waitUntil(self.registration.showNotification(String(data.title || 'Sterling').slice(0, 120), {
    body: String(data.body || '').slice(0, 620),
    tag: String(data.tag || '').slice(0, 160),
    icon: '/icons/sterling-192.png',
    badge: '/icons/sterling-192.png',
    data: {url: safeDestination(data.url)}
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const destination = safeDestination(event.notification.data?.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    // Do not focus a random same-origin window while leaving it on the wrong
    // URL: explicitly navigate the matched Sterling tab first.
    const appPath = new URL('./', self.registration.scope).pathname;
    const app = windows.find(client => {
      try { return new URL(client.url).pathname === appPath; } catch { return false; }
    });
    if (app) {
      try {
        if (typeof app.navigate === 'function') {
          const navigated = await app.navigate(destination);
          if (navigated) { await navigated.focus(); return; }
        }
      } catch { /* Browser blocked navigation: open a correct app URL below. */ }
    }
    await self.clients.openWindow(destination);
  })());
});

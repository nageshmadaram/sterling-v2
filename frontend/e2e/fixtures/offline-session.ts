import type { Page } from '@playwright/test';

/** UI contracts cannot inherit the operator's broker session or make broker writes. */
export async function offlineSession(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('sterling.auth.access', 'browser-contract-access');
    sessionStorage.setItem('sterling.auth.refresh', 'browser-contract-refresh');
  });
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/auth/preferences') return route.fulfill({ json: { values: {}, initialized: true } });
    if (path === '/api/v1/auth/me') {
      return route.fulfill({ json: { user_id: 'default', username: 'Browser fixture', role: 'admin', administrator: true, policy_revision: 1, features: { kite: true, tv: false, radio: false }, visibility: { kite: true, tv: true, radio: true } } });
    }
    if (path === '/api/v1/auth/remember' || path === '/api/v1/auth/refresh') {
      return route.fulfill({ json: { access_token: 'browser-contract-access', refresh_token: 'browser-contract-refresh' } });
    }
    if (path === '/api/v1/operations/family' || path.startsWith('/api/v1/snapback/family/')) {
      return route.fallback(); // These tests install their explicit response/refusal handlers first.
    }
    return route.fulfill({ status: 503, json: { detail: 'Not supplied by the isolated browser fixture' } });
  });
  await page.routeWebSocket('**/api/v1/stream/ws**', socket => {
    socket.onMessage(message => {
      try {
        if (JSON.parse(String(message)).action === 'auth') socket.send(JSON.stringify({ type: 'auth_ok', user_id: 'default' }));
      } catch { /* unrelated frame */ }
    });
  });
}

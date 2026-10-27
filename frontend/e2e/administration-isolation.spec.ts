import { expect, test } from '@playwright/test';

for (const viewport of [{ width: 1440, height: 1050 }, { width: 390, height: 844 }]) {
  test(`administration stays independent from Kite at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const requests: string[] = [], sockets: string[] = [], errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('websocket', socket => sockets.push(socket.url()));
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      requests.push(path);
      expect(route.request().headers()['x-sterling-view']).toBeUndefined();
      const sections = { brokers: { visible: false, grants: [] }, telegram: { visible: false, grants: [] }, integrations: { visible: false, grants: [] } };
      const responses: Record<string, unknown> = {
        '/api/v1/auth/me': { user_id: 'default', username: 'Primary', role: 'admin', administrator: true, features: { kite: false, tv: false, radio: false }, policy_revision: 0 },
        '/api/v1/auth/remember': { access_token: 'synthetic', refresh_token: 'synthetic' },
        '/api/v1/settings/capabilities': { user_id: 'default', revision: 0, active: true, administrator: true, sections },
        '/api/v1/auth/users': { current_user_id: 'default', users: [
          { id: 'default', username: 'Primary', primary: true, active: true, role: 'admin', password_set: true },
          { id: 'member', username: 'Member', active: true, role: 'user', password_set: true },
        ] },
        '/api/v1/auth/users/member/features': { user_id: 'member', revision: 0, features: { kite: false, tv: true, radio: false } },
        '/api/v1/auth/users/member/configuration-access': { user_id: 'member', revision: 0, active: true, administrator: false, sections },
      };
      expect(responses[path], `Unexpected API request: ${path}`).toBeDefined();
      await route.fulfill({ json: responses[path] });
    });
    await page.addInitScript(() => localStorage.setItem('sterling.view', 'simulation'));
    await page.goto('/administration?broker_login=1');
    await page.getByLabel('Account', { exact: true }).selectOption('member');
    await expect(page.getByRole('heading', { name: 'Broker settings', exact: true })).toBeVisible();
    await expect(page.getByText('Hidden; configuration actions are disabled.')).toHaveCount(3);
    await expect(page.getByRole('checkbox', { name: 'Space (TV)', exact: true })).toBeChecked();
    expect(requests.some(path => path.startsWith('/api/v1/kite'))).toBe(false);
    expect(requests.some(path => path.includes('preferences'))).toBe(false);
    // Vite's development hot-reload socket is unrelated to the trading runtime.
    const applicationSockets = sockets.filter(value => {
      const url = new URL(value);
      return !(url.pathname === '/' && url.searchParams.has('token'));
    });
    expect(applicationSockets).toEqual([]);
    expect(errors).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`administration-${viewport.width}.png`), fullPage: true });
  });
}

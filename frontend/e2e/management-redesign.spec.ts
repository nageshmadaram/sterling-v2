import { expect, test } from '@playwright/test';

test('mobile Kite has one touchable header and the Sterling icon switches UIs', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  // Keep the real terminal shell, header, menus and CSS; isolate trading panes.
  await page.route('**/src/components/kite/KiteTab.tsx*', route => route.fulfill({ contentType: 'text/javascript', body: 'export function KiteTab(){return null}' }));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const session = { user_id: 'member', username: 'Member', role: 'user', administrator: false, features: { kite: true, tv: true, radio: true }, policy_revision: 1 };
    if (path === '/api/v1/auth/me') { await route.fulfill({ json: session }); return; }
    if (path === '/api/v1/auth/preferences') { await route.fulfill({ json: { initialized: true, values: {} } }); return; }
    if (path === '/api/v1/auth/profile') { await route.fulfill({ json: { user_id: 'member', username: 'Member', display_name: 'Member' } }); return; }
    if (path === '/api/v1/sim/status') { await route.fulfill({ status: 403, json: { detail: 'Owner only' } }); return; }
    if (/\/media\/radio\/access$/.test(path)) { await route.fulfill({ json: { document_url: '/api/v1/media/radio/document' } }); return; }
    if (/\/media\/radio\/document$/.test(path)) { await route.fulfill({ contentType: 'text/html', body: '<html><body>Echo workspace</body></html>' }); return; }
    if (path === '/api/v1/kite/accounts') { await route.fulfill({ json: { accounts: [], active_account_id: null } }); return; }
    if (path === '/api/v1/kite/telegram/targets') { await route.fulfill({ json: { targets: [] } }); return; }
    await route.fulfill({ json: {} });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Kite', exact: true }).click();
  const logo = page.getByRole('button', { name: 'Sterling Home', exact: true });
  await expect(logo).toBeVisible();
  await expect(page.locator('.workspace-header')).toHaveCount(0);
  await expect(page.locator('.sterling-mobile-command-row')).toHaveCount(0);
  await page.getByRole('button', { name: 'Menu', exact: true }).tap();
  const menu = page.getByRole('dialog', { name: 'Sterling mobile menu' });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Astrology', exact: true })).toHaveCount(1);
  await page.getByRole('button', { name: 'Close mobile menu', exact: true }).tap();
  await logo.tap();
  const selector = page.getByRole('dialog', { name: 'Choose your workspace' });
  await expect(selector).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('kite-workspace-overlay.png'), fullPage: true });
  await selector.getByRole('button', { name: 'Open Echo', exact: true }).tap();
  await expect(page.frameLocator('iframe').locator('body')).toContainText('Echo workspace');
  await expect(page.locator('.term-root')).toHaveCount(0);
  await page.getByRole('button', { name: 'Choose workspace', exact: true }).tap();
  await expect(page.getByRole('dialog', { name: 'Choose your workspace' })).toBeVisible();
});

test('a denied workspace creates an admin request, and approval enables it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let admin = false, approved = false, signedIn = true;
  const requests: Array<{ id: string; user_id: string; username: string; feature: string; status: string; created_at_ms: number }> = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/api/v1/auth/logout') { signedIn = false; await route.fulfill({ json: { ok: true } }); return; }
    if (path === '/api/v1/auth/login') { admin = new URLSearchParams(request.postData() || '').get('username') === 'admin'; signedIn = true; await route.fulfill({ json: { access_token: 'synthetic', refresh_token: 'synthetic' } }); return; }
    if (path === '/api/v1/auth/me') { await route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? { user_id: admin ? 'default' : 'member', username: admin ? 'Admin' : 'Member', role: admin ? 'admin' : 'user', administrator: admin, features: { kite: false, tv: false, radio: !admin && approved }, policy_revision: approved ? 1 : 0 } : { detail: 'Sign in' } }); return; }
    if (path === '/api/v1/settings/access-requests') { if (!requests.length) requests.push({ id: 'request-1', user_id: 'member', username: 'Member', feature: 'radio', status: 'pending', created_at_ms: Date.now() }); await route.fulfill({ json: requests[0] }); return; }
    if (path === '/api/v1/admin/access-requests/request-1') { expect(admin).toBe(true); approved = true; requests[0].status = 'approved'; await route.fulfill({ json: requests[0] }); return; }
    if (path === '/api/v1/admin/access-requests') { await route.fulfill({ json: { requests } }); return; }
    if (path === '/api/v1/settings/capabilities') { await route.fulfill({ json: { active: true, administrator: admin, sections: {} } }); return; }
    if (path === '/api/v1/auth/users') { await route.fulfill({ json: { users: [], current_user_id: 'default' } }); return; }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Request access to Echo', exact: true }).tap();
  await expect(page.getByRole('status')).toContainText('has been sent to your administrator');
  expect(requests).toHaveLength(1);
  await page.getByRole('button', { name: 'Sign out', exact: true }).tap();
  await page.getByLabel('Username', { exact: true }).fill('admin'); await page.getByLabel('Password', { exact: true }).fill('Synthetic-password-123');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Administration', exact: true })).toBeVisible();
  await expect(page.getByText('No features enabled')).toHaveCount(0);
  await page.getByRole('button', { name: /1 access request needs your review/ }).click();
  await page.getByRole('button', { name: 'Approve', exact: true }).tap();
  await expect(page.getByText('Approved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).tap();
  await page.getByLabel('Username', { exact: true }).fill('member'); await page.getByLabel('Password', { exact: true }).fill('Synthetic-password-123');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open Echo', exact: true })).toBeVisible();
});

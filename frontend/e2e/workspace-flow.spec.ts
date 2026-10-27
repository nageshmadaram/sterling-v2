import { expect, test } from '@playwright/test';

const hiddenSections = () => ({ brokers: { visible: false, grants: [] as unknown[] }, telegram: { visible: false, grants: [] as unknown[] }, integrations: { visible: false, grants: [] as unknown[] } });

test('fresh TV-only login opens the chooser, then protected TV, with no Kite runtime', async ({ page }, testInfo) => {
  const requests: string[] = [], scripts: string[] = [], errors: string[] = [];
  let signedIn = false;
  let preferences: Record<string, string | null> = {};
  let initialized = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.resourceType() === 'script') scripts.push(request.url()); });
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    if (path === '/api/v1/auth/login') { signedIn = true; await route.fulfill({ json: { access_token: 'e2e-access', refresh_token: 'e2e-refresh' } }); return; }
    if (path === '/api/v1/auth/me') {
      await route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? { user_id: 'viewer', username: 'Viewer', role: 'user', administrator: false, features: { kite: false, tv: true, radio: false }, policy_revision: 1 } : { detail: 'Sign in' } }); return;
    }
    if (path === '/api/v1/auth/preferences') {
      if (route.request().method() === 'PATCH') {
        const body = route.request().postDataJSON();
        expect(body.values).not.toHaveProperty('sterling_ui_state');
        preferences = { ...preferences, ...body.values }; initialized = true;
      }
      await route.fulfill({ json: { initialized, values: preferences } }); return;
    }
    if (path === '/api/v1/media/tv/access') {
      await route.fulfill({ headers: { 'Content-Type': 'application/json', 'Set-Cookie': 'sterling_media_tv=synthetic; Path=/api/v1/media/tv/document; HttpOnly; SameSite=Lax' }, body: JSON.stringify({ document_url: '/api/v1/media/tv/document', expires_in: 600 }) }); return;
    }
    if (path === '/api/v1/media/tv/document') {
      expect(route.request().headers()['authorization']).toBeUndefined();
      expect(route.request().headers()['cookie']).toContain('sterling_media_tv=synthetic');
      await route.fulfill({ contentType: 'text/html', headers: { 'Content-Security-Policy': "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'" },
        body: "<!doctype html><html><body>Protected TV document<script>window.received=[];addEventListener('message',e=>received.push(e.data));parent.postMessage({type:'sterling.tv.ready'},'*')</script></body></html>" }); return;
    }
    if (path === '/api/v1/auth/logout') { signedIn = false; await route.fulfill({ json: { ok: true } }); return; }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?sterling_login=1&broker_login=1');
  await expect(page.getByRole('checkbox', { name: 'Remember this device' })).not.toBeChecked();
  await page.getByLabel('Username', { exact: true }).fill('viewer');
  await page.getByLabel('Password', { exact: true }).fill('Synthetic-password-123');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome, Viewer.' })).toBeVisible();
  expect(await page.locator('iframe').count()).toBe(0);
  await expect(page.getByRole('button', { name: /Open Kite/ })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('launcher.png'), fullPage: true });
  await page.getByRole('button', { name: /Open Space/ }).click();
  const frame = page.frameLocator('iframe[title="Sterling TV player"]');
  await expect(frame.locator('body')).toContainText('Protected TV document');
  expect(initialized).toBe(true);
  expect(requests.filter(path => path.startsWith('/api/v1/kite'))).toEqual([]);
  expect(scripts.some(url => url.includes('KiteRuntime'))).toBe(false);
  expect(requests.includes('/api/v1/auth/remember')).toBe(false);
  expect(await page.evaluate(() => localStorage.getItem('sterling.auth.access'))).toBeNull();
  expect(errors).toEqual([]);
  await page.getByRole('button', { name: 'Home', exact: true }).click();
  await expect(page.locator('iframe')).toHaveCount(0);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('form', { name: 'Sterling login' })).toBeVisible();
});

test('central administration creates a user, grants TV, and saves assigned broker and Telegram settings', async ({ page }, testInfo) => {
  const users = [{ id: 'default', username: 'Primary', role: 'admin', primary: true, active: true, password_set: true }];
  const brokers: unknown[] = [], targets: unknown[] = [];
  let revision = 0;
  let features = { kite: false, tv: false, radio: false };
  const sections = hiddenSections();
  const requests: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    requests.push(path);
    const session = { user_id: 'default', username: 'Primary', role: 'admin', administrator: true, features: { kite: false, tv: false, radio: false }, policy_revision: 0 };
    if (path === '/api/v1/auth/me') { await route.fulfill({ json: session }); return; }
    if (path === '/api/v1/settings/capabilities') { await route.fulfill({ json: { user_id: 'default', active: true, administrator: true, revision: 0, sections: hiddenSections() } }); return; }
    if (path === '/api/v1/auth/users') {
      if (method === 'POST') { const body = request.postDataJSON(); expect(body.role).toBe('user'); users.push({ id: 'member', username: body.username, role: 'user', primary: false, active: true, password_set: true }); await route.fulfill({ json: users[1] }); return; }
      await route.fulfill({ json: { current_user_id: 'default', users } }); return;
    }
    if (path === '/api/v1/auth/users/member/features') {
      if (method === 'PUT') { const body = request.postDataJSON(); expect(body.expected_revision).toBe(revision); features = body.features; revision++; }
      await route.fulfill({ json: { user_id: 'member', features, revision } }); return;
    }
    if (path === '/api/v1/auth/users/member/configuration-access') {
      await route.fulfill({ json: { user_id: 'member', revision, active: true, administrator: false, sections } }); return;
    }
    if (path === '/api/v1/admin/brokers') {
      if (method === 'POST') {
        const body = request.postDataJSON(); expect(body.user_id).toBe('member');
        brokers.push({ id: 'K-SYNTHETIC', user_id: 'member', label: body.label, has_credentials: true, connected: false, updated_at_ms: 1 });
        await route.fulfill({ json: brokers[0] }); return;
      }
      await route.fulfill({ json: { brokers } }); return;
    }
    if (path === '/api/v1/admin/users/member/telegram') {
      if (method === 'POST') { const body = request.postDataJSON(); targets.push({ id: 'T-SYNTHETIC', label: body.label, chat_id: body.chat_id, enabled: true, bot_token_set: true }); await route.fulfill({ json: targets[0] }); return; }
      await route.fulfill({ json: { targets } }); return;
    }
    throw new Error(`Unexpected admin API ${method} ${path}`);
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Administration', exact: true }).click();
  await page.getByRole('button', { name: '+ Add user', exact: true }).click();
  await page.getByLabel('Username', { exact: true }).fill('NewViewer');
  await page.getByLabel('Password', { exact: true }).fill('Synthetic-password-123');
  await page.getByLabel('Repeat password', { exact: true }).fill('Synthetic-password-123');
  await page.getByRole('button', { name: 'Save user', exact: true }).click();
  await page.getByLabel('Account', { exact: true }).selectOption('member');
  await page.getByRole('checkbox', { name: 'Space (TV)', exact: true }).check();
  await page.getByRole('button', { name: 'Save features', exact: true }).click();
  await expect(page.getByText('Feature grants saved.')).toBeVisible();
  await page.getByRole('button', { name: 'Broker connections', exact: true }).click();
  await page.getByLabel('Assign to Sterling user', { exact: true }).selectOption('member');
  await page.getByLabel('Connection label', { exact: true }).fill('Viewer Kite');
  await page.getByLabel('API key', { exact: true }).fill('synthetic-key');
  await page.getByLabel('API secret', { exact: true }).fill('synthetic-secret');
  await page.getByRole('button', { name: 'Save broker connection', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Viewer Kite', exact: true })).toBeVisible();
  await expect(page.getByLabel('API secret', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Telegram integrations', exact: true }).click();
  await page.getByLabel('Integration owner', { exact: true }).selectOption('member');
  await page.getByLabel('Destination label', { exact: true }).fill('Viewer alerts');
  await page.getByLabel('Bot token', { exact: true }).fill('synthetic-token');
  await page.getByLabel('Recipient chat ID', { exact: true }).fill('12345');
  await page.getByRole('button', { name: 'Save destination', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Viewer alerts', exact: true })).toBeVisible();
  expect(requests.some(path => path.startsWith('/api/v1/kite'))).toBe(false);
  expect(requests.some(path => path.endsWith('/test'))).toBe(false);
  await page.screenshot({ path: testInfo.outputPath('central-administration.png'), fullPage: true });
});

for (const width of [390, 1440]) {
  test(`Kite Echo Space chooser and both switch paths work at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    let signedIn = false;
    let initialized = false;
    let preferences: Record<string, string | null> = {};
    const features = { kite: true, tv: true, radio: true };
    const requests: string[] = [];
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      requests.push(path);
      if (path === '/api/v1/auth/login') { signedIn = true; await route.fulfill({ json: { access_token: 'synthetic-access', refresh_token: 'synthetic-refresh' } }); return; }
      if (path === '/api/v1/auth/me') { await route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? { user_id: 'viewer', username: 'Viewer', role: 'user', administrator: false, features, policy_revision: 1 } : { detail: 'Sign in' } }); return; }
      if (path === '/api/v1/auth/preferences') {
        if (route.request().method() === 'PATCH') {
          const body = route.request().postDataJSON();
          expect(body.values).not.toHaveProperty('sterling_ui_state');
          preferences = { ...preferences, ...body.values }; initialized = true;
        }
        await route.fulfill({ json: { initialized, values: preferences } }); return;
      }
      const match = path.match(/^\/api\/v1\/media\/(tv|radio)\/(access|document)$/);
      if (match) {
        const [, feature, action] = match;
        if (action === 'access') await route.fulfill({ json: { document_url: `/api/v1/media/${feature}/document`, expires_in: 600 } });
        else await route.fulfill({ contentType: 'text/html', body: `<html><body>Protected ${feature} document</body></html>` });
        return;
      }
      throw new Error(`Unexpected request ${path}`);
    });
    await page.goto('/radio?sterling_login=1');
    await page.getByLabel('Username', { exact: true }).fill('viewer');
    await page.getByLabel('Password', { exact: true }).fill('Synthetic-password-123');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByText('Choose your workspace.', { exact: true })).toBeVisible();
    for (const name of ['Kite', 'Echo', 'Space']) await expect(page.getByRole('button', { name: new RegExp(`Open ${name}`) })).toBeVisible();
    await expect(page.locator('iframe')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Open Space/ })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath(`chooser-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: /Open Echo/ }).click();
    await expect(page.frameLocator('iframe').locator('body')).toContainText('Protected radio document');
    expect(initialized).toBe(true);
    await page.locator('.workspace-settings > summary').click();
    await page.getByRole('button', { name: 'Switch workspace', exact: true }).click();
    await expect(page.getByText('Choose your workspace.', { exact: true })).toBeVisible();
    await expect(page.locator('iframe')).toHaveCount(0);
    await page.getByRole('button', { name: /Open Space/ }).click();
    await expect(page.frameLocator('iframe').locator('body')).toContainText('Protected tv document');
    await page.getByRole('button', { name: 'Echo', exact: true }).click();
    await expect(page.frameLocator('iframe').locator('body')).toContainText('Protected radio document');
    expect(initialized).toBe(true);
    await page.getByRole('button', { name: 'Radio display settings', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Switch workspace', exact: true }).click();
    await expect(page.getByText('Choose your workspace.', { exact: true })).toBeVisible();
    expect(requests.some(path => path.startsWith('/api/v1/kite/'))).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  });
}

import { expect, test } from '@playwright/test';

// Current directory-based administration: the route must remain independent
// from Kite and never create broker runtime authority just to edit access.
for (const viewport of [{ width: 1440, height: 1050 }, { width: 390, height: 844 }]) {
  test(`administration stays independent from Kite at ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const requests: string[] = [], sockets: string[] = [], errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('websocket', socket => sockets.push(socket.url()));
    const sections = {
      brokers: { visible: false, grants: [] },
      telegram: { visible: false, grants: [] },
      integrations: { visible: false, grants: [] },
    };
    const defaultUser = {
      id: 'default', username: 'Primary', display_name: 'Primary', photo: '',
      primary: true, active: true, role: 'admin', password_set: true,
      revision: 0, online: false, last_login_ms: null, activity: [],
      features: { kite: false, tv: false, radio: false },
      visibility: { kite: true, tv: true, radio: true },
      brokers: [], telegram: [],
    };
    const member = {
      ...defaultUser, id: 'member', username: 'Member', display_name: 'Member',
      primary: false, role: 'user', features: { kite: false, tv: true, radio: false },
    };

    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      requests.push(path);
      expect(request.headers()['x-sterling-view']).toBeUndefined();
      // No wildcards or 200-by-default: an unexpected API call must fail this
      // test rather than silently supplying fictitious authorization evidence.
      const responses: Record<string, unknown> = {
        '/api/v1/auth/me': {
          user_id: 'default', username: 'Primary', role: 'admin',
          administrator: true, features: defaultUser.features,
          visibility: defaultUser.visibility, policy_revision: 0,
        },
        '/api/v1/auth/profile': {
          user_id: 'default', username: 'Primary', display_name: 'Primary',
          photo: '', role: 'admin',
        },
        '/api/v1/admin/directory': {
          users: [defaultUser, member], brokers: [], current_user_id: 'default',
        },
        '/api/v1/admin/access-requests': { requests: [] },
        '/api/v1/settings/capabilities': {
          user_id: 'default', revision: 0, active: true,
          administrator: true, sections,
        },
        '/api/v1/auth/users/member/features': {
          user_id: 'member', revision: 0, features: member.features,
          visibility: member.visibility,
        },
        '/api/v1/auth/users/member/configuration-access': {
          user_id: 'member', revision: 0, active: true,
          administrator: false, sections,
        },
      };
      expect(request.method()).toBe('GET');
      expect(responses[path], `Unexpected API request: ${path}`).toBeDefined();
      await route.fulfill({ json: responses[path] });
    });
    await page.goto('/administration?broker_login=1');
    await expect(page.getByRole('heading', { name: 'Administration', exact: true })).toBeVisible();

    // Select an existing user through the actual directory, rather than the
    // superseded manage-user combobox and legacy tab selectors.
    const directory = page.getByRole('region', { name: 'User directory' });
    await directory.getByRole('button', { name: /Member/ }).click();
    const management = page.getByRole('navigation', { name: 'User management' });
    await management.getByRole('button', { name: 'Access', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Broker settings', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Telegram settings', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Integration settings', exact: true })).toBeVisible();
    await expect(page.getByRole('checkbox', { name: 'Allow tv access', exact: true })).toBeChecked();
    await expect(page.getByRole('checkbox', { name: 'Allow kite access', exact: true })).not.toBeChecked();

    // No browser-side permission state may turn on trading or broker config.
    expect(requests.some(path => path.startsWith('/api/v1/kite'))).toBe(false);
    expect(requests.some(path => path.includes('/preferences'))).toBe(false);
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

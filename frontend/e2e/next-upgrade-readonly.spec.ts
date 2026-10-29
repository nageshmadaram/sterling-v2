import { expect, test, type Page } from '@playwright/test';

// Real Chromium UI and responsive layout; synthetic in-browser API responses only.
// No backend instance, credentials, provider, production resource, or network writes.
type Feature = 'kite' | 'radio' | 'tv';
type Session = { user_id: string; username: string; role: string; administrator: boolean; features: Record<Feature, boolean>;
  visibility: Record<Feature, boolean>; policy_revision: number };
async function isolate(page: Page, policy: Session) {
  const seen: string[] = [];
  const blocked: string[] = [];
  const externalBlocked: string[] = [];
  let session = policy;
  // No third-party transport (Vite's localhost assets still load).
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port === '5194') {
      await route.continue(); return;
    }
    externalBlocked.push(route.request().url());
    await route.abort();
  });
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    seen.push(req.method() + ' ' + path);
    // The *only* intercepted non-GET is the synthetic media-cookie request.
    // Playwright fulfills it locally. No request reaches an API server.
    if (req.method() === 'POST' && path === '/api/v1/media/radio/access') {
      await route.fulfill({ json: { document_url: '/api/v1/media/radio/document', expires_in: 600 } }); return;
    }
    if (req.method() !== 'GET') {
      blocked.push(req.method() + ' ' + path);
      await route.fulfill({ status: 405, json: { detail: 'No state changes in browser QA' } }); return;
    }
    if (path === '/api/v1/auth/me') { await route.fulfill({ json: session }); return; }
    if (path === '/api/v1/auth/profile') {
      await route.fulfill({ json: { user_id: session.user_id, username: session.username, display_name: session.username, photo: '' } }); return;
    }
    if (path === '/api/v1/admin/directory') {
      await route.fulfill({ json: {current_user_id:session.user_id,brokers:[],users:[{
        id:session.user_id,username:session.username,role:session.role,active:true,online:false,
        primary:session.administrator,features:session.features,revision:session.policy_revision,
        activity:[],brokers:[],telegram:[],last_login_ms:null,password_set:true
      }]}}); return;
    }
    if (path === '/api/v1/admin/access-requests') {
      await route.fulfill({json:{requests:[]}}); return;
    }
    if (path === '/api/v1/settings/capabilities') {
      await route.fulfill({json:{user_id:session.user_id,revision:session.policy_revision,active:true,
        administrator:session.administrator,sections:{brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}}}}); return;
    }
    if (path === '/api/v1/auth/preferences') {
      await route.fulfill({ json: { initialized: true, values: {} } }); return;
    }
    if (path === '/api/v1/media/radio/document') {
      await route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body>Isolated synthetic radio document</body></html>' }); return;
    }
    await route.fulfill({ status: 404, json: { detail: 'Read-only fixture: endpoint not used' } });
  });
  return { seen, blocked, externalBlocked, update(next: Session) { session = next; } };
}

const base: Session = {
  user_id: 'synthetic-member', username: 'Synthetic Member', role: 'user', administrator: false,
  features: { kite: false, radio: false, tv: true },
  visibility: { kite: true, radio: true, tv: false }, policy_revision: 1,
};

for (const width of [390, 1440]) {
  test(`direct-hidden page, browser Back, selector focus and Escape at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const fixture = await isolate(page, { ...base });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/tv');
    await expect(page.getByRole('heading', { name: 'Workspace unavailable' })).toBeVisible();
    await expect(page.locator('iframe')).toHaveCount(0);
    await page.getByRole('button', { name: '← Back to workspaces' }).click();
    await expect(page.getByRole('heading', { name: 'Choose your workspace' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Space' })).toHaveCount(0);
    const trigger = page.getByRole('button', { name: 'Choose workspace', exact: true });
    await trigger.focus();
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: 'Choose your workspace' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Close workspace selector' }).click();
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await trigger.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await page.getByRole('button', { name: 'Request access to Kite' }).click();
    await expect(page.getByRole('heading', { name: 'Kite is not enabled for your account' })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'Choose your workspace' })).toBeVisible();
    await page.screenshot({ path: info.outputPath(`synthetic-hidden-${width}.png`), fullPage: true });
    expect(errors).toEqual([]);
    expect(fixture.blocked).toEqual([]);
    expect(fixture.seen.some(x => /\/api\/v1\/(kite|admin|orders)/.test(x))).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  });

  test(`visible media page evicted when policy hides it at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const enabled: Session = { ...base, features: { kite: false, radio: true, tv: false },
      visibility: { kite: true, radio: true, tv: true } };
    const fixture = await isolate(page, enabled);
    await page.goto('/radio');
    await expect(page.getByRole('main', { name: 'Echo workspace' })).toBeVisible();
    const observedReads = fixture.seen.filter(x => x === 'GET /api/v1/auth/me').length;
    fixture.update({ ...enabled, visibility: { ...enabled.visibility, radio: false }, policy_revision: 2 });
    // React Query subscribes to visibility/focus; this provokes the next policy read.
    await page.evaluate(() => {
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect.poll(() => fixture.seen.filter(x => x === 'GET /api/v1/auth/me').length,
      { timeout: 20000 }).toBeGreaterThan(observedReads);
    await expect(page.getByRole('heading', { name: 'Workspace unavailable' }),
      'After the periodic authoritative policy read, the mounted media page must be evicted').toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('main', { name: 'Echo workspace' })).toHaveCount(0);
    await expect(page.locator('iframe')).toHaveCount(0);
    // Preference sync attempted PATCH; the harness denied it before network I/O.
    expect(fixture.blocked.every(x => x === 'PATCH /api/v1/auth/preferences')).toBe(true);
    expect(fixture.seen.some(x => x.startsWith('POST /api/v1/media/radio/access'))).toBe(true);
    // The POST above was fulfilled entirely by Playwright, not a server.
  });
}

for (const width of [390, 1440]) {
  test(`admin read-only navigation and immediate unmount after revoked policy at ${width}px`, async ({ page }) => {
    await page.setViewportSize({width,height:844});
    const enabled: Session = {...base,role:'admin',administrator:true,
      features:{kite:false,radio:false,tv:false},
      visibility:{kite:true,radio:true,tv:true},policy_revision:3};
    const fixture = await isolate(page, enabled);
    const errors: string[] = [];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('/administration');
    await expect(page.getByRole('heading',{name:'Administration',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
    const modal = page.getByRole('dialog',{name:'Choose your workspace'});
    await expect(modal.getByRole('button',{name:'Administration'})).toBeVisible();
    await modal.getByRole('button',{name:'Close workspace selector'}).click();
    const observed = fixture.seen.filter(x=>x==='GET /api/v1/auth/me').length;
    fixture.update({...enabled,administrator:false,role:'user',policy_revision:4});
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    await expect.poll(()=>fixture.seen.filter(x=>x==='GET /api/v1/auth/me').length,
      {timeout:20000}).toBeGreaterThan(observed);
    await expect(page.getByRole('heading',{name:'Administrator access required'})).toBeVisible({timeout:20000});
    await expect(page.getByRole('heading',{name:'Administration',exact:true})).toHaveCount(0);
    expect(fixture.seen).toContain('GET /api/v1/admin/directory');
    expect(fixture.blocked).toEqual([]);
    expect(errors).toEqual([]);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  });
}

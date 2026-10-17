import { test, expect } from '@playwright/test';
import { emaCampaign, emaConfig, emaFeature, emaReport, emaSetup } from '../src/test/emaTrendFixtures';
import type { EmaTrendCampaign, EmaTrendMode } from '../src/types/emaTrend';

// API-contract browser acceptance. Canonical broker/runtime integration is tested by the backend suite.
for (const [mode, label] of [['swing', 'Swing'], ['intraday', 'Intraday'], ['scalping', 'Scalp'], ['ultra_scalping', 'Ultrascalp']] as const) {
  test(`${label}: Connect → auto-scan → Profiles menu → chart → paper fill → quarter → close`, async ({ page }) => {
    let config = emaConfig();
    config.config = { ...config.defaults };
    let rows: ReturnType<typeof emaSetup>[] = [];
    let campaign: EmaTrendCampaign | null = null;
    let scanned = false;
    let completedAt = 0;
    const mutations: Array<{ path: string; body: Record<string, unknown> }> = [];
    const unknown: string[] = [];
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace('/api/v1/ema-trend', '');
      const body = request.method() === 'GET' ? {} : request.postDataJSON() || {};
      if (request.method() !== 'GET') mutations.push({ path, body });
      let payload: unknown;
      if (path === '/config' && request.method() === 'GET') payload = config;
      else if (path === '/config' && request.method() === 'PATCH') {
        expect(body.expected_revision).toBe(config.revision);
        config = { ...config, config: { ...config.config, ...body.changes }, revision: config.revision + 1 }; payload = config;
      } else if (path === '/snapshot') {
        const profile = url.searchParams.get('mode');
        payload = { ...config,
          rows: rows.filter(row => !profile || row.mode === profile),
          campaigns: campaign && (!profile || campaign.mode === profile) ? [campaign] : [],
          scan: scanned ? { id: 'job-1', status: 'COMPLETED', progress: 1, errors: [], finished_ms: completedAt } : null,
          generated_ms: Date.now(), next_cursor: null,
        };
      }
      else if (path === '/scan') {
        expect(body.modes).toEqual([mode]);
        rows = [{ ...emaSetup(mode), config_revision: config.revision }]; scanned = true; completedAt = Date.now();
        payload = { id: 'job-1', status: 'COMPLETED', progress: 1, errors: [] };
      } else if (path === '/scans/job-1') payload = { id: 'job-1', status: 'COMPLETED', progress: 1, errors: [] };
      else if (path === '/chart') {
        expect(url.searchParams.get('mode')).toBe(mode);
        payload = { symbol: 'NSE:TCS', mode, signal_id: rows[0]?.id, features: Array.from({ length: 4 }, (_, i) => {
          const f = emaFeature(); return { ...f, bar: { ...f.bar, close_ms: f.bar.close_ms - (3 - i) * 60000 } };
        }), blockers: [] };
      } else if (path === '/plans') {
        expect(body).toEqual({ signal_id: `setup-${mode}`, expected_config_revision: config.revision });
        payload = { id: 'plan-1', revision: 1, status: 'READY', blockers: [] };
      } else if (path === '/plans/plan-1/arm') {
        expect(body.execution_mode).toBe('PAPER');
        expect(body).not.toHaveProperty('quantity');
        campaign = { ...emaCampaign(mode), q0: 103, qty: 103, last_mark: '100', config_revision: config.revision, below21: 0,
          lots: [{ fill_id: 'fill-1', qty: 103, price: '100' }] };
        rows = [];
        payload = { status: 'ACKNOWLEDGED', campaign, execution: { order_id: 'paper-1' } };
      } else if (path === `/campaigns/campaign-${mode}`) payload = { campaign, events: [] };
      else if (path === `/campaigns/campaign-${mode}/actions`) {
        expect(body.expected_revision).toBe(campaign!.revision);
        const quantity = body.action === 'close' ? 0 : campaign!.qty - Math.floor(campaign!.q0 / 4);
        campaign = { ...campaign!, qty: quantity, revision: campaign!.revision + 1, state: quantity ? 'OPEN' : 'CLOSED',
          lots: quantity ? [{ fill_id: 'fill-1', qty: quantity, price: '100' }] : [] };
        payload = { status: 'ACKNOWLEDGED', campaign };
      } else if (path === '/runs') payload = { items: [] };
      else if (path === '/replay') payload = { id: 'run-1', status: 'COMPLETED', symbol: 'NSE:TCS', mode, result: emaReport(mode) };
      else if (path === '/runs/run-1') payload = { id: 'run-1', status: 'COMPLETED', symbol: 'NSE:TCS', mode, result: emaReport(mode) };
      else { unknown.push(url.pathname); payload = { detail: 'Unexpected API path in isolated acceptance fixture' }; }
      await route.fulfill({ status: unknown.includes(url.pathname) ? 500 : 200, json: payload });
    });
    await page.goto('/e2e/fixtures/ema-trend-harness.html');
    await page.getByLabel('EMA Trend equities').fill('NSE:TCS');
    await page.getByLabel('Paper allocation (₹)').fill('100000');
    await page.getByRole('switch', { name: '10/21 EMA + Supertrend engine' }).click();
    if (mode !== 'swing') {
      await page.getByRole('checkbox', { name: label, exact: true }).check();
      await page.getByRole('checkbox', { name: 'Swing', exact: true }).uncheck();
    }
    await page.getByRole('button', { name: 'Apply changes' }).click();
    await expect(page.getByRole('button', { name: 'Apply changes' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Signals workspace' }).click();
    await expect(page.getByRole('group', { name: 'EMA Trend profiles' })).toBeVisible();
    await page.getByRole('button', { name: 'PROFILES' }).click();
    const profiles = page.getByRole('group', { name: 'EMA Trend visible profiles' });
    await expect(profiles).toBeVisible();
    for (const other of ['Swing', 'Intraday', 'Scalp', 'Ultrascalp'].filter(item => item !== label)) {
      await profiles.getByRole('checkbox', { name: other, exact: true }).uncheck();
    }
    await expect(profiles.getByRole('checkbox', { name: label, exact: true })).toBeChecked();
    await page.getByRole('button', { name: /PROFILES/ }).click();
    await expect(page.getByRole('button', { name: 'Chart TCS' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Recorded replay' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Pause paper entries' })).toHaveCount(0);
    await expect(page.getByRole('group', { name: 'Visible columns' })).toHaveCount(0);
    await page.getByRole('button', { name: /Columns/i }).first().click();
    await expect(page.getByRole('group', { name: 'Visible columns' })).toBeVisible();
    await page.getByRole('button', { name: /Columns/i }).first().click();
    await page.getByRole('button', { name: 'Chart TCS' }).click();
    await expect(page.getByRole('region', { name: 'EMA Trend evidence chart' })).toBeVisible();
    await expect(page.getByText('DEMA50', { exact: true })).toBeVisible();
    await expect(page.getByText(/completed context ST: UP/)).toBeVisible();
    await page.getByRole('button', { name: 'Signals workspace' }).click();
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    await page.getByRole('button', { name: 'Execute paper plan' }).click();
    await expect(page.getByText('103/103 shares', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Open TCS detail' }).click();
    await expect(page.getByText('PAPER · CASH EQUITY')).toBeVisible();
    await page.getByRole('button', { name: 'Reduce 25% paper' }).click();
    await expect(page.getByText(/Shares: 78/)).toBeVisible();
    await page.getByRole('button', { name: 'Close paper campaign' }).click();
    await expect(page.getByText(/Shares: 0/)).toBeVisible();
    await page.getByRole('button', { name: 'Close detail' }).click();
    expect(unknown).toEqual([]);
    expect(mutations.filter(m => m.path === '/scan')).toHaveLength(1);
    expect(mutations.filter(m => m.path.endsWith('/arm'))).toHaveLength(1);
    expect(mutations.filter(m => m.path.endsWith('/actions'))).toHaveLength(2);
    await page.screenshot({ path: `test-results/ema-trend/${mode}-workflow.png`, fullPage: true });
  });
}


test('EMA empty universe shows one clear setup action; Profiles and Columns menus remain usable', async ({ page }) => {
  const config = emaConfig();
  config.config = { ...config.config, enabled: true, symbols: [] };
  const writes: string[] = [];
  await page.route('**/api/v1/ema-trend/**', async route => {
    const req = route.request();
    if (req.method() !== 'GET') writes.push(req.url());
    const path = new URL(req.url()).pathname;
    const payload = path.endsWith('/config') ? config :
      path.includes('/snapshot') ? { ...config, rows: [], campaigns: [],
        scan: { status: 'IDLE', progress: 0, errors: [] } } : { items: [] };
    await route.fulfill({ status: 200, json: payload });
  });
  await page.goto('/e2e/fixtures/ema-trend-harness.html');
  await page.getByRole('button', { name: 'Signals workspace' }).click();
  await expect(page.getByText(/No equities selected\. Open EMA Trend settings/)).toBeVisible();
  await expect(page.getByText('Nothing to show.')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Recorded replay' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Pause paper entries' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Emergency close (simulation)' })).toHaveCount(0);
  await page.getByRole('button', { name: 'PROFILES' }).click();
  const profiles = page.getByRole('group', { name: 'EMA Trend visible profiles' });
  for (const label of ['Swing', 'Intraday', 'Scalp', 'Ultrascalp']) {
    await expect(profiles.getByRole('checkbox', { name: label, exact: true })).toBeChecked();
  }
  await profiles.getByRole('checkbox', { name: 'Swing', exact: true }).uncheck();
  await expect(profiles.getByRole('checkbox', { name: 'Swing', exact: true })).not.toBeChecked();
  await profiles.getByText('Show all profiles').click();
  await expect(profiles.getByRole('checkbox', { name: 'Swing', exact: true })).toBeChecked();
  await page.getByRole('button', { name: /PROFILES/ }).click();
  await page.getByRole('button', { name: /Columns/i }).first().click();
  await expect(page.getByRole('group', { name: 'Visible columns' })).toBeVisible();
  const isPortalled = await page.getByRole('group', { name: 'Visible columns' }).evaluate(node =>
    node.parentElement === document.body && window.getComputedStyle(node).position === 'fixed');
  expect(isPortalled).toBe(true);
  expect(writes).toEqual([]);
});

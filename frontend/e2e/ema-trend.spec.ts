import { test, expect } from '@playwright/test';
import { emaCampaign, emaConfig, emaFeature, emaReport, emaSetup } from '../src/test/emaTrendFixtures';
import type { EmaTrendCampaign, EmaTrendMode } from '../src/types/emaTrend';

// API-contract browser acceptance. Canonical broker/runtime integration is tested by the backend suite.
for (const [mode, label] of [['swing', 'Swing'], ['intraday', 'Intraday'], ['scalping', 'Scalp'], ['ultra_scalping', 'Ultrascalp']] as const) {
  test(`${label}: Connect → shared row → chart → paper fill → quarter → close → replay`, async ({ page }, testInfo) => {
    let config = emaConfig();
    config.config = { ...config.defaults };
    let rows: ReturnType<typeof emaSetup>[] = [];
    let campaign: EmaTrendCampaign | null = null;
    let scanned = false;
    const replayReport = { ...emaReport(mode), dataset_manifest: { content_sha256: 'recorded-dataset-fixture',
      signal: { bar_count: 300, quality_status: 'ISSUES_FOUND', issues: [{ code: 'MISSING_EXPECTED_BAR', count: 2 }] },
      context: { bar_count: 100, quality_status: 'PARTIAL_CHECKS', issues: [] } } };
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
      } else if (path === '/snapshot') payload = { ...config, supervision: { status: 'RUNNING', age_ms: 2000, errors: [] }, rows, campaigns: campaign ? [campaign] : [], scan: scanned ? { id: 'job-1', status: 'COMPLETED', progress: 1, errors: [] } : null };
      else if (path === '/scan') {
        expect(body.modes).toEqual([mode]);
        rows = [{ ...emaSetup(mode), config_revision: config.revision }]; scanned = true;
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
      else if (path === '/replay') payload = { id: 'run-1', status: 'COMPLETED', symbol: 'NSE:TCS', mode, result: replayReport };
      else if (path === '/runs/run-1') payload = { id: 'run-1', status: 'COMPLETED', symbol: 'NSE:TCS', mode, result: replayReport };
      else { unknown.push(url.pathname); payload = { detail: 'Unexpected API path in isolated acceptance fixture' }; }
      await route.fulfill({ status: unknown.includes(url.pathname) ? 500 : 200, json: payload });
    });
    await page.goto('/e2e/fixtures/ema-trend-harness.html');
    await page.getByLabel('EMA Trend instruments').fill('NSE:TCS');
    await page.getByLabel('Paper allocation (₹)').fill('100000');
    await page.getByRole('switch', { name: '10/21 EMA + Supertrend engine' }).click();
    if (mode !== 'swing') {
      await page.getByRole('checkbox', { name: label, exact: true }).check();
      await page.getByRole('checkbox', { name: 'Swing', exact: true }).uncheck();
    }
    await page.getByRole('button', { name: 'Apply changes' }).click();
    await expect(page.getByRole('button', { name: 'Apply changes' })).toHaveCount(0);
    await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-${mode}-settings.png` });
    await page.getByRole('button', { name: 'Signals workspace' }).click();
    await page.getByRole('button', { name: 'Scan now' }).click();
    await expect(page.getByRole('button', { name: 'Chart TCS' })).toBeVisible();
    await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-${mode}-board.png` });
    await page.getByRole('button', { name: 'Chart TCS' }).click();
    await expect(page.getByRole('region', { name: 'EMA Trend evidence chart' })).toBeVisible();
    await expect(page.getByText('DEMA50', { exact: true })).toBeVisible();
    await expect(page.getByText(/completed context ST: UP/)).toBeVisible();
    await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-${mode}-chart.png` });
    await page.getByRole('button', { name: 'Signals workspace' }).click();
    await page.getByRole('button', { name: 'Paper', exact: true }).click();
    await page.getByRole('button', { name: 'Execute paper plan' }).click();
    await expect(page.getByText('103/103 shares', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'EMA Trend paper operations' })).toContainText('103 confirmed shares');
    await expect(page.getByRole('region', { name: 'EMA Trend paper operations' })).toContainText('Supervision: RUNNING');
    await page.getByRole('button', { name: 'Open TCS detail' }).click();
    await expect(page.getByText('PAPER · CASH EQUITY')).toBeVisible();
    await page.getByRole('button', { name: 'Reduce 25% paper' }).click();
    await expect(page.getByText(/Shares: 78/)).toBeVisible();
    await page.getByRole('button', { name: 'Close paper campaign' }).click();
    await expect(page.getByText(/Shares: 0/)).toBeVisible();
    await page.getByRole('button', { name: 'Close detail' }).click();
    await page.getByRole('button', { name: 'Recorded replay', exact: true }).click();
    await page.getByLabel('Replay profile').selectOption(mode);
    await page.getByRole('button', { name: 'Replay recorded bars', exact: true }).click();
    await expect(page.getByRole('region', { name: 'EMA Trend lifecycle report' })).toBeVisible();
    await expect(page.getByRole('table', { name: 'Fill legs · read-only' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Recorded dataset quality' })).toContainText('MISSING EXPECTED BAR: 2');
    expect(unknown).toEqual([]);
    expect(mutations.filter(m => m.path.endsWith('/arm'))).toHaveLength(1);
    expect(mutations.filter(m => m.path.endsWith('/actions'))).toHaveLength(2);
    await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-${mode}-workflow.png`, fullPage: true });
  });
}

test('Exact option selector → whole-lot paper entry → quarter → close', async ({ page }, testInfo) => {
  const mode = 'scalping' as const;
  const name = 'NIFTY26NOV25000CE', symbol = `NFO:${name}`;
  const contract = { exchange: 'NFO', tradingsymbol: name, instrument_type: 'CE' as const, lot_size: 25, tick_size: '.05', expiry: '2026-11-26', execution_vehicle: 'OPTION' as const, product: 'MIS' as const, capital_model: 'CASH_OR_PREMIUM', signal_source: 'SELECTED_CONTRACT_OHLC' };
  let config = emaConfig(); config.config = { ...config.defaults };
  let signal: ReturnType<typeof emaSetup> | null = null;
  let campaign: EmaTrendCampaign | null = null;
  let armed = 0;
  const unknown: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname.replace('/api/v1/ema-trend', '');
    const body = req.method() === 'GET' ? {} : req.postDataJSON();
    let data: unknown;
    if (path === '/config' && req.method() === 'GET') data = config;
    else if (path === '/config') { config = { ...config, config: { ...config.config, ...body.changes }, revision: config.revision + 1 }; data = config; }
    else if (path === '/instruments') data = { items: [
      { symbol, name: 'NIFTY', exchange: 'NFO', instrument_type: 'CE', lot_size: 25, tick_size: '.05', expiry: contract.expiry, execution_supported: true, blocker: null, contract },
      { symbol: 'MCX:GOLD26NOVFUT', name: 'GOLD', exchange: 'MCX', instrument_type: 'FUT', lot_size: 1, tick_size: '1', expiry: '2026-11-30', execution_supported: false, blocker: 'MCX_MULTIPLIER_CALENDAR_ACCOUNTING_NOT_IMPLEMENTED', contract: null },
    ] };
    else if (path === '/snapshot') data = { ...config, supervision: { status: 'RUNNING', age_ms: 1000, errors: [] }, rows: signal ? [signal] : [], campaigns: campaign ? [campaign] : [], scan: null };
    else if (path === '/scan') { signal = { ...emaSetup(mode), symbol, contract, config_revision: config.revision }; data = { id: 'option-scan', status: 'COMPLETED', progress: 100, errors: [] }; }
    else if (path === '/scans/option-scan') data = { id: 'option-scan', status: 'COMPLETED', progress: 100, errors: [] };
    else if (path === '/plans') data = { id: 'option-plan', revision: 1, status: 'READY', blockers: [], contract };
    else if (path === '/plans/option-plan/arm') {
      expect(body).not.toHaveProperty('quantity'); expect(body.execution_mode).toBe('PAPER'); armed += 1;
      campaign = { ...emaCampaign(mode), symbol, product: 'MIS', lot_size: 25, instrument_type: 'CE', expiry: contract.expiry, qty: 100, q0: 100, below10: 0, below21: 0, lots: [{ fill_id: 'option-fill', qty: 100, price: '100' }] };
      signal = null; data = { status: 'ACKNOWLEDGED', campaign };
    } else if (path === `/campaigns/campaign-${mode}`) data = { campaign, events: [] };
    else if (path === `/campaigns/campaign-${mode}/actions`) {
      const qty = body.action === 'close' ? 0 : campaign!.qty - 25;
      campaign = { ...campaign!, qty, revision: campaign!.revision + 1, state: qty ? 'OPEN' : 'CLOSED', lots: qty ? [{ fill_id: 'option-fill', qty, price: '100' }] : [] };
      data = { status: 'ACKNOWLEDGED', campaign };
    } else { unknown.push(path); data = { detail: 'Unexpected contract request' }; }
    await route.fulfill({ status: unknown.includes(path) ? 500 : 200, json: data });
  });
  await page.goto('/e2e/fixtures/ema-trend-harness.html');
  await page.getByLabel('EMA Trend instrument search').fill('NIFTY');
  await page.getByRole('checkbox', { name: symbol, exact: true }).check();
  await expect(page.getByRole('checkbox', { name: 'MCX:GOLD26NOVFUT', exact: true })).toBeDisabled();
  await page.getByLabel('Paper allocation (₹)').fill('100000');
  await page.getByRole('switch', { name: '10/21 EMA + Supertrend engine' }).click();
  await page.getByRole('checkbox', { name: 'Scalp', exact: true }).check();
  await page.getByRole('checkbox', { name: 'Swing', exact: true }).uncheck();
  await page.getByRole('button', { name: 'Apply changes' }).click();
  await expect(page.getByRole('button', { name: 'Apply changes' })).toHaveCount(0);
  expect(config.config.symbols).toEqual([symbol]);
  await page.getByLabel('EMA Trend instrument search').scrollIntoViewIfNeeded();
  await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-contract-selector.png` });
  await page.getByRole('button', { name: 'Signals workspace' }).click();
  await page.getByRole('button', { name: 'Scan now' }).click();
  await page.getByRole('button', { name: 'Paper', exact: true }).click();
  await expect(page.getByText('PAPER · LONG OPTION')).toBeVisible();
  await page.getByRole('button', { name: 'Execute paper plan' }).click();
  await expect(page.getByText('100/100 units', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Open ${name} detail` }).click();
  await page.getByRole('button', { name: 'Reduce 25% paper' }).click();
  await expect(page.getByText(/Units: 75/)).toBeVisible();
  await page.getByRole('button', { name: 'Close paper campaign' }).click();
  await expect(page.getByText(/Units: 0/)).toBeVisible();
  expect(armed).toBe(1); expect(unknown).toEqual([]);
  await page.screenshot({ path: `test-results/ema-trend/${testInfo.project.name}-contract-workflow.png` });
});

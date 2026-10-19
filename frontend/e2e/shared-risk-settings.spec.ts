import { expect, test } from '@playwright/test';
import { offlineSession } from './fixtures/offline-session';

test('selected risk overrides persist without enabling trading', async ({ page }, testInfo) => {
  await offlineSession(page);
  await page.route('**/api/v1/kite/status', route => route.fulfill({ json: { connected: true, has_credentials: true, user_name: 'Browser fixture' } }));
  let profile = { revision: 0, enabled: false, strategies: [] as string[], allocation_inr: null as number | null, max_daily_loss_inr: null as number | null, per_trade_risk_inr: null as number | null };
  let writes = 0;
  await page.route('**/api/v1/config/shared-risk', route => {
    if (route.request().method() === 'PUT') {
      writes++;
      profile = { ...route.request().postDataJSON(), revision: profile.revision + 1 };
    }
    return route.fulfill({ json: profile });
  });
  await page.goto('/');
  await page.getByRole('button', { name: /^Tools/ }).click();
  await page.getByRole('menuitem', { name: 'Connect', exact: true }).click();
  await page.getByLabel('Settings section', { exact: true }).selectOption('mode');
  await expect(page.getByText('Saving these settings does not enable trading.')).toBeVisible();
  await expect(page.getByLabel('Allocation cap (₹)', { exact: true })).toHaveValue('');
  await page.getByRole('checkbox', { name: 'Snapback', exact: true }).check();
  await page.getByLabel('Allocation cap (₹)', { exact: true }).fill('10000');
  await page.getByLabel('Per-trade risk cap (₹)', { exact: true }).fill('15000');
  await expect(page.getByRole('button', { name: 'Save shared risk settings', exact: true })).toBeDisabled();
  await page.getByLabel('Per-trade risk cap (₹)', { exact: true }).fill('1000');
  await page.getByRole('switch', { name: 'Off', exact: true }).click();
  await page.getByRole('button', { name: 'Save shared risk settings', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Shared risk settings saved' })).toBeVisible();
  expect(profile).toMatchObject({ enabled: true, strategies: ['snapback'], allocation_inr: 10000, per_trade_risk_inr: 1000, max_daily_loss_inr: null });
  expect(writes).toBe(1);
  await page.screenshot({ path: testInfo.outputPath('shared-risk-settings.png'), fullPage: true });
  await page.reload();
  await expect(page.getByLabel('Allocation cap (₹)', { exact: true })).toHaveValue('10000');
  await expect(page.getByRole('checkbox', { name: 'Gamma-Unwind', exact: true })).not.toBeChecked();
});

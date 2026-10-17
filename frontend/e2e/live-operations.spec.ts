import { expect, test } from '@playwright/test';
import { offlineSession } from './fixtures/offline-session';

/**
 * The Operations screen, end to end.
 *
 * This is the screen a non-technical operator is expected to read instead of a
 * terminal, so the properties under test are the ones that would mislead a
 * person rather than a developer: an unreachable backend must never look
 * healthy, a broker position Sterling did not open must be unmistakable and
 * un-actionable, and every UNKNOWN must stay visible instead of resolving into
 * a comfortable zero.
 */

const PATH = '**/api/v1/operations/family';

const RELEASE_SHA = '03ee3d367a31390bcede7eb856527e026e16954c';

function payload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    runtime_sha: RELEASE_SHA,
    release_tag: 'sterling-runtime-1.0',
    environment: 'production',
    system_status: 'HEALTHY',
    production_mode: 'LIVE',
    new_risk_allowed: true,
    new_risk_blockers: [],
    next_safe_action: 'Nothing. Trading is enabled.',
    safety: {
      safety_state: 'NORMAL',
      execution_control_state: 'CLEAN',
      new_risk_allowed: true,
      family_stop_engaged: false,
      live_execution_enabled: true,
      blockers: [],
    },
    broker: {
      account_id: 'AA0595',
      binding_id: 'binding-1',
      connected: true,
      broker_flatness: 'PASS',
      broker_state_readable: true,
      broker_state_detail: '',
      broker_observed_at: new Date().toISOString(),
      managed_positions: [],
      external_positions: [],
      unresolved_intents: 0,
      open_exposure: 'PASS',
    },
    deployment: {
      static_egress: 'PASS',
      expected_egress_ip: '203.0.113.7',
      observed_egress_ip: '203.0.113.7',
      deployment_identity: 'PASS',
      lake_mount: 'PASS',
      network_path: 'PASS',
      market_freshness: 'PASS',
      last_tick_at: new Date().toISOString(),
      backend_reachable: true,
    },
    security: {
      environment: 'production',
      production_security: 'PASS',
      stored_secret_count: 4,
      dev_fallback_in_use: false,
      last_migration_at: null,
    },
    operator: { stop_available: true, resume_available: false, stop_engaged: false },
    ...overrides,
  };
}

function serve(page: any, body: Record<string, unknown>) {
  return page.route(PATH, (route: any) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    }),
  );
}

async function openOperationsTab(page: any) {
  await offlineSession(page);
  // The session guard renders a full-page overlay when Kite reads offline, and
  // in CI there is no broker session — the overlay then intercepts the click on
  // the Operations tab and every test in this file fails on navigation rather than
  // on what it is about. Stub the session as connected first.
  await page.route('**/api/v1/kite/status', (route: any) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: true,
        user_name: 'Madaram Nagesh',
        kite_user_id: 'AA0595',
        has_credentials: true,
      }),
    }),
  );
  await page.route('**/api/v1/operations/egress', (route: any) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        expected_ip: null,
        observed_ip: null,
        observed_at: null,
        router_generation: null,
        verified: null,
        reason: 'no static address is registered for this deployment',
        changed_from: null,
        record_command: 'sterlingctl egress record <ip>',
      }),
    }),
  );

  await page.goto('/');

  const tools = page.getByRole('button', { name: /^Tools/ });
  await expect(tools).toBeVisible({ timeout: 30_000 });
  await tools.click();
  await page.getByRole('menuitem', { name: 'More', exact: true }).click();

  const operations = page.getByTestId('more-tab-operations');
  await expect(operations).toBeVisible({ timeout: 15_000 });
  await operations.click();

  // The container renders in both states, and the unreachable alert lives
  // INSIDE it — an `.or()` of the two matches both and trips strict mode.
  await expect(page.getByTestId('live-operations')).toBeVisible({ timeout: 15_000 });
}

test.describe('Operations', () => {
  test('an unreachable backend never shows a healthy screen', async ({ page }) => {
    await page.route(PATH, (route: any) =>
      route.fulfill({ status: 503, body: '{"detail":"unavailable"}' }),
    );
    await openOperationsTab(page);

    await expect(page.getByTestId('ops-unreachable')).toBeVisible();
    await expect(page.getByLabel('trading mode')).toContainText('UNREACHABLE');
    await expect(page.getByText('TRADING ENABLED')).toHaveCount(0);
  });

  test('live mode is stated with trading enabled', async ({ page }) => {
    await serve(page, payload());
    await openOperationsTab(page);

    await expect(page.getByLabel('trading mode')).toContainText('LIVE — TRADING ENABLED');
    await expect(page.getByText(RELEASE_SHA.slice(0, 12))).toBeVisible();
  });

  test('the kill switch reads as new entries off, not as a healthy state', async ({ page }) => {
    const base = payload();
    await serve(page, {
      ...base,
      production_mode: 'LIVE_HALTED',
      safety: { ...(base.safety as Record<string, unknown>), live_execution_enabled: false },
    });
    await openOperationsTab(page);

    await expect(page.getByLabel('trading mode')).toContainText('NEW ENTRIES OFF');
    await expect(page.getByLabel('global live execution switch: FAIL')).toBeVisible();
  });

  test('an external position is visible, unmanaged, and has no action control', async ({ page }) => {
    await serve(
      page,
      payload({
        new_risk_allowed: false,
        safety: {
          safety_state: 'NORMAL',
          execution_control_state: 'CLEAN',
          new_risk_allowed: false,
          family_stop_engaged: false,
          live_execution_enabled: true,
          blockers: [
            {
              code: 'external_broker_exposure',
              severity: 'BLOCK',
              message: 'the broker holds 1 position Sterling did not open',
              source: 'safety_supervisor',
            },
          ],
        },
        broker: {
          ...(payload().broker as Record<string, unknown>),
          broker_flatness: 'FAIL',
          open_exposure: 'FAIL',
          external_positions: [
            {
              source: 'BROKER_EXTERNAL',
              managed_by_sterling: false,
              account: 'AA0595',
              instrument: 'CDSL26SEP1500CE',
              exchange: 'NFO',
              product: 'NRML',
              quantity: 16625,
              broker_avg_price: 3.271429,
              last_price: 3.35,
              unrealised_pnl: 1306.24,
              discovery_reason: 'RECONCILIATION_UNKNOWN_POSITION',
              protection_known: false,
              sterling_intent: null,
              sterling_fill: null,
              observed_at: new Date().toISOString(),
            },
          ],
        },
      }),
    );
    await openOperationsTab(page);

    const unmanaged = page.getByLabel('Positions not managed by Sterling');
    await expect(unmanaged).toBeVisible();
    await expect(unmanaged).toContainText('CDSL26SEP1500CE');
    await expect(unmanaged).toContainText('Sterling does not manage these');
    // No Protect/Exit affordance: Sterling does not manage it.
    await expect(unmanaged.getByRole('button')).toHaveCount(0);

    await expect(page.getByText('NEW RISK BLOCKED')).toBeVisible();
    await expect(page.getByText('external_broker_exposure')).toBeVisible();
  });

  test('the development fallback key is shown as a blocker', async ({ page }) => {
    await serve(
      page,
      payload({
        security: {
          environment: 'development',
          production_security: 'UNKNOWN',
          stored_secret_count: 4,
          dev_fallback_in_use: true,
          last_migration_at: null,
        },
      }),
    );
    await openOperationsTab(page);

    await expect(page.getByText('DEV FALLBACK KEY IN USE')).toBeVisible();
  });

  test('stop all new trades reaches the backend', async ({ page }) => {
    const stopCalls: string[] = [];
    await page.route('**/api/v1/snapback/family/stop-new-trades*', (route: any) => {
      stopCalls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"halted":true}' });
    });
    await serve(page, payload());
    await openOperationsTab(page);

    await page.getByRole('button', { name: /STOP ALL NEW TRADES/ }).click();
    await expect.poll(() => stopCalls.length).toBeGreaterThan(0);
  });

  test('a refused resume shows the refusal', async ({ page }) => {
    await page.route('**/api/v1/snapback/family/resume-new-trades*', (route: any) =>
      route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"RECOVERY_REQUIRED"}' }),
    );
    await serve(
      page,
      payload({ operator: { stop_available: true, resume_available: true, stop_engaged: true } }),
    );
    await openOperationsTab(page);

    await page.getByRole('button', { name: /Resume new trades/ }).click();
    await page.getByRole('button', { name: /Confirm resume/ }).click();
    await expect(page.getByRole('alert').filter({ hasText: /RECOVERY_REQUIRED|refus|failed/i }).first())
      .toBeVisible({ timeout: 10_000 });
  });
});

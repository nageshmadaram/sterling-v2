import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { isBrokerAccountScopedQuery, resetBrokerAccountQueries } from '../brokerAccountQueryReset';

describe('Kite account-scoped React Query cache', () => {
  it('selects read-only and operational Kite strategy caches, not account identity', () => {
    for (const key of [
      ['kite-engine-signals', 'today'], ['kite-positions'],
      ['kite-orders'], ['kite-login-url'], ['kite-engine-open-positions'],
      ['snapback-snapshot', 'live'], ['snapback-history', 30],
      ['gamma-move-snapshot', 'today'], ['gamma-move-history', 60],
      ['ema-trend', 'snapshot'], ['adaptive-edge-signals'],
    ]) expect(isBrokerAccountScopedQuery({ queryKey: key })).toBe(true);
    for (const key of [
      ['kite-status'], ['kite-accounts'], ['ui-settings'], ['notifications'], [23],
    ]) expect(isBrokerAccountScopedQuery({ queryKey: key })).toBe(false);
  });

  it('removes old account signals and position caches without losing account status', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['kite-status'], { account_id: 'account-b' });
    client.setQueryData(['kite-accounts'], { active_id: 'account-b' });
    client.setQueryData(['kite-engine-signals', 'today'], { rows: [{ symbol: 'OLD' }] });
    client.setQueryData(['snapback-snapshot', 'live'], { rows: [{ symbol: 'OLD' }] });
    client.setQueryData(['gamma-move-snapshot', 'today'], { rows: [{ symbol: 'OLD' }] });
    client.setQueryData(['ema-trend', 'snapshot'], { rows: [{ symbol: 'OLD' }] });
    client.setQueryData(['kite-positions'], [{ symbol: 'OLD' }]);
    client.setQueryData(['ui-settings'], { density: 'compact' });
    await resetBrokerAccountQueries(client);
    for (const key of [
      ['kite-engine-signals', 'today'], ['snapback-snapshot', 'live'],
      ['gamma-move-snapshot', 'today'], ['ema-trend', 'snapshot'], ['kite-positions'],
    ]) expect(client.getQueryData(key)).toBeUndefined();
    expect(client.getQueryData(['kite-status'])).toEqual({ account_id: 'account-b' });
    expect(client.getQueryData(['kite-accounts'])).toEqual({ active_id: 'account-b' });
    expect(client.getQueryData(['ui-settings'])).toEqual({ density: 'compact' });
    client.clear();
  });
});

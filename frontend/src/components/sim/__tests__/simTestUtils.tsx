import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { SimStatus, SimStrategies, StrategyKey, StrategyMode } from '../../../hooks/useSim';

export const NOW = new Date('2026-10-09T05:00:00Z').getTime();   // 10:30 IST

export function strategies(modes: Partial<Record<StrategyKey, StrategyMode>> = {}, conflict: StrategyKey[] = []): SimStrategies {
  const arm = { master: false, strategy: false, armed: false, enabled: true };
  const out: SimStrategies = {};
  for (const key of ['supertrend', 'snapback', 'gamma'] as StrategyKey[]) {
    out[key] = {
      strategy: key,
      mode: modes[key] ?? 'MANUAL',
      conflict: conflict.includes(key),
      live: { ...arm, armed: modes[key] === 'AUTO' },
      simulation: { ...arm, armed: modes[key] === 'SIMULATION', configured: true },
    };
  }
  return out;
}

export function makeStatus(over: Partial<SimStatus> = {}): SimStatus {
  return {
    virtual: true,
    capital: 1_000_000,
    cash: 900_000,
    equity: 1_002_500,
    today_pnl: 2_500,
    open_positions: 2,
    runtime: { running: true, last_pass_ms: Date.now() - 2_000, passes: 10, last_error: '', session_open: true },
    price_feed: { available: true, detail: '' },
    strategies: strategies(),
    message: 'SIMULATION - virtual money, no real orders',
    ...over,
  };
}

/** An error the way `utils/api.ts` throws it: an object `detail` arrives as JSON text. */
export function apiError(status: number, detail: unknown): Error {
  const e = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)) as Error & { status: number };
  e.status = status;
  return e;
}

export function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...render(ui, { wrapper }) };
}

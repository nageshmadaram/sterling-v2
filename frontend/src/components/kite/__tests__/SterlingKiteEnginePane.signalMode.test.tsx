import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const cfg = {
  engine_enabled: true, trail_target: 'fast', exit_mode: 'one_red', exit_aligned_trail: false,
  strike_moneyness: ['ATM'], scan_source: 'spot',
  scan_expiries: ['weekly', 'monthly'], scan_expiries_indices: null, scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'], scan_stocks: [], scan_all_stocks: false, auto_execute: false,
  risk_sizing: true, risk_pct: 1.0, max_lots: 10, expiry_square_off_days: 1, time_stop_bars: 0,
  stop_mode: 'both', directional_mode: false, vehicle: 'otm_options',
  enabled_vehicles: ['otm_options', 'deep_itm_options'], itm_depth: 'ITM10', target_delta: null,
  futures_expiry: 'near', adx_min: null, atr_pct_min: null, block_entry_minutes_before_close: 0,
  max_spread_pct: null, min_oi: null, max_daily_loss_pct: null, wire_risk_infra: false,
};

function makeRow(source: 'spot' | 'navigator' = 'spot') {
  return {
    underlying: source === 'navigator' ? 'SENSEX' : 'NIFTY 50',
    token: source === 'navigator' ? 265 : 256265,
    exchange: 'NFO', regime: 'BULL',
    alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
    spot: 24010, stop_loss: 23900, score: 85, timestamp_ms: 1_700_000_000_000,
    source, is_active: true, is_fresh: true,
    legs: source === 'navigator' ? [] : [{
      moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25JUN24000CE',
      strike: 24000, expiry: '2026-06-26', lot_size: 75, token: 44001, is_active: true,
    }],
  };
}

const patchSpy = vi.fn();

function mockRows(rows: ReturnType<typeof makeRow>[], overrides: Record<string, unknown> = {}) {
  vi.doMock('../../../hooks/useSterlingKiteEngine', () => ({
    useEngineConfig: () => ({ data: { ...cfg, ...overrides } }),
    useSetEngineConfig: () => ({ mutate: vi.fn() }),
    usePatchEngineConfig: () => ({ mutate: patchSpy, isPending: false, isError: false, error: null }),
    useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
    useEngineSignals: () => ({
      data: { generated_ms: 1, scanning: false, scanning_label: '', rows, next_scan_ms: 0, auto_scan: false, market_open: true },
    }),
    useRunScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(() => Promise.resolve()), isPending: false }),
    useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
    useStockRegistry: () => ({ data: [] }),
  }));
}

async function renderPane(rows: ReturnType<typeof makeRow>[], overrides: Record<string, unknown> = {}) {
  mockRows(rows, overrides);
  const { SterlingKiteEnginePane: Pane } = await import('../SterlingKiteEnginePane');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Pane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('SterlingKiteEnginePane — SuperTrend-only release surface', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
    patchSpy.mockClear();
  });

  it('ignores a stale persisted Navigator lens and still renders SuperTrend only', async () => {
    localStorage.setItem('kite_st_signal_mode', 'navigator');
    await renderPane([makeRow('spot'), makeRow('navigator')]);

    expect(screen.getAllByText('NIFTY 50').length).toBeGreaterThan(0);
    expect(screen.queryByText('SENSEX')).not.toBeInTheDocument();
    expect(screen.queryByTitle(/^VIEW —/)).not.toBeInTheDocument();
  });

  it('keeps SuperTrend SOURCE and EXIT controls visible', async () => {
    await renderPane([makeRow('spot')]);
    expect(screen.getByTitle(/^SOURCE —/)).toBeInTheDocument();
    expect(screen.getByTitle(/^EXIT —/)).toBeInTheDocument();
  });

  it('shows the SuperTrend-off state when only a retired Navigator row exists', async () => {
    await renderPane([makeRow('navigator')], { engine_enabled: false });
    expect(screen.getByText('Engine is off')).toBeInTheDocument();
    expect(screen.getByText(/SuperTrend scanning is disabled/i)).toBeInTheDocument();
    expect(screen.queryByText('SENSEX')).not.toBeInTheDocument();
  });

  it('can turn SuperTrend back on from the off state', async () => {
    await renderPane([], { engine_enabled: false });
    fireEvent.click(screen.getByRole('button', { name: /Enable Engine/i }));
    expect(patchSpy).toHaveBeenCalledWith(
      { engine_enabled: true },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });
});

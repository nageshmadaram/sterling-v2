import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

const navDecision = {
  decision_id: 'nav_abc123', schema_version: 1, config_revision: 1, model_versions: {},
  generated_at_ms: 1_700_000_000_000, bar_close_ms: 1_700_000_000_000, activation_watermark_ms: 0,
  base_signal_id: 's1', trigger: 'base_fresh', direction: 'long', status: 'CONFIRMED',
  base_score: 85, suite_score: 80, effective_score: 82, execution_eligible: true, data_quality: 'ok',
  reason_codes: ['OK'], avwap: null, volatility: null, option_flow: null, gamma: null,
};

function row(source: 'spot' | 'navigator' = 'spot') {
  return {
    underlying: source === 'navigator' ? 'NIFTY BANK' : 'NIFTY 50',
    token: source === 'navigator' ? 260105 : 256265,
    exchange: 'NFO', regime: 'BULL',
    alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
    spot: 24010, stop_loss: 23900, score: 85, timestamp_ms: 1_700_000_000_000,
    source, is_active: true, is_fresh: true, navigator: navDecision,
    legs: source === 'navigator' ? [] : [{
      moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25JUN24000CE',
      strike: 24000, expiry: '2026-06-26', lot_size: 75, token: 44001, is_active: true,
    }],
  };
}

function mockEngineHooks(rows = [row()]) {
  vi.doMock('../../../hooks/useSterlingKiteEngine', () => ({
    useEngineConfig: () => ({ data: cfg }),
    useSetEngineConfig: () => ({ mutate: vi.fn() }),
    usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
    useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
    useEngineSignals: () => ({
      data: { generated_ms: 1, scanning: false, scanning_label: '', rows, next_scan_ms: 0, auto_scan: false, market_open: true },
    }),
    useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
    useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
    useStockRegistry: () => ({ data: [] }),
  }));
}

async function renderPane(rows = [row()]) {
  mockEngineHooks(rows);
  const { SterlingKiteEnginePane: Pane } = await import('../SterlingKiteEnginePane');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Pane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('SterlingKiteEnginePane — retired Navigator isolation', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it('does not render Navigator evidence as a release badge on a SuperTrend row', async () => {
    await renderPane([row('spot')]);
    expect(screen.getAllByText('NIFTY 50').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Nav CONFIRMED/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Navigator idea/i)).not.toBeInTheDocument();
  });

  it('filters Navigator-originated rows from the release board', async () => {
    await renderPane([row('spot'), row('navigator')]);
    expect(screen.getAllByText('NIFTY 50').length).toBeGreaterThan(0);
    expect(screen.queryByText('NIFTY BANK')).not.toBeInTheDocument();
  });

  it('does not expose the retired VIEW/Navigator controls', async () => {
    await renderPane([row('spot')]);
    expect(screen.queryByTitle(/^VIEW —/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Navigator only|Where both agree|Show Navigator setups/i)).not.toBeInTheDocument();
  });
});

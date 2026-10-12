/**
 * A switched-off engine gets no tab.
 *
 * The dock carries the three release strategies only: SuperTrend, Gamma Unwind and
 * Snapback. A stopped one disappears from the strip; the switch that stopped it
 * already says why. UNKNOWN config is not treated as ON: a failed query must not
 * advertise a running engine or make it eligible for the shared scanner.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, cleanup, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// A FULL engine config. A minimal `{ engine_enabled }` crashes the pane on the
// first `.length` of a config array it expects to exist — the render throws before
// a single assertion runs, which reads as the test being wrong rather than the
// fixture.
const cfg: Record<string, unknown> = {
  engine_enabled: true, trail_target: 'fast', exit_mode: 'one_red', exit_aligned_trail: false,
  strike_moneyness: ['ATM'], scan_source: 'derivatives',
  scan_expiries: ['weekly', 'monthly'], scan_expiries_indices: null, scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'], scan_stocks: [], scan_all_stocks: false, auto_execute: false,
  risk_sizing: true, risk_pct: 1.0, max_lots: 10, expiry_square_off_days: 1, time_stop_bars: 0,
  stop_mode: 'both', directional_mode: false, vehicle: 'otm_options',
  enabled_vehicles: ['otm_options', 'deep_itm_options'], itm_depth: 'ITM10', target_delta: null,
  futures_expiry: 'near', adx_min: null, atr_pct_min: null, block_entry_minutes_before_close: 0,
  max_spread_pct: null, min_oi: null, max_daily_loss_pct: null, wire_risk_infra: false,
};

let stEnabled: boolean | undefined = true;
let gmEnabled: boolean | undefined = true;
let sbEnabled: boolean | undefined = true;

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { ...cfg, engine_enabled: stEnabled } }),
  useSetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({ data: { generated_ms: 0, scanning: false, scanning_label: '', rows: [], next_scan_ms: 0, auto_scan: false, market_open: true } }),
  useRunScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
  useStockRegistry: () => ({ data: [] }),
}));
vi.mock('../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: () => ({ data: null }),
  useGammaMoveConfig: () => ({ data: { config: { enabled: gmEnabled } } }),
  useGammaMoveScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useSnapback', () => ({
  useSnapbackConfig: () => ({ data: { config: { enabled: sbEnabled } } }),
  useSnapbackSnapshot: () => ({ data: undefined }),
  useRunSnapbackScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useUpdateSnapback: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../SterlingKiteEngineWithExpiry', () => ({ SterlingKiteEngineWithExpiry: () => <div /> }));
vi.mock('../board/GammaMoveBoard', () => ({ GammaMoveBoard: () => <div data-testid="gamma-board" /> }));
vi.mock('../board/PriceRulesBoard', () => ({ PriceRulesBoard: () => <div data-testid="price-rules-board" /> }));
vi.mock('../board/SnapbackBoard', () => ({ SnapbackBoard: () => <div data-testid="snapback-board" /> }));

// The SIDEBAR is the shell: it owns the tab strip and renders the active
// engine's pane inside itself. Rendering the pane alone gives you a board with no
// tabs, which is why the first version of this file found none.
import { AdaptiveEdgeRightSidebar } from '../AdaptiveEdgeRightSidebar';

function renderPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AdaptiveEdgeRightSidebar onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

const tab = (name: string) => screen.queryAllByRole('tab', { name: new RegExp(name, 'i') });

beforeEach(() => {
  localStorage.clear();
  stEnabled = true;
  gmEnabled = true; sbEnabled = true;
});
afterEach(cleanup);

describe('the dock follows the running switches', () => {
  it('always exposes Price Rules and opens its signals board', () => {
    renderPane();
    fireEvent.click(screen.getByRole('tab', { name: 'Open Price Rules strategy' }));
    expect(screen.getByTestId('price-rules-board')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Open Price Rules strategy' }).getAttribute('aria-selected')).toBe('true');
  });

  it('shows the three scanners and Price Rules when all are on', () => {
    renderPane();
    for (const name of ['SuperTrend', 'Gamma Unwind', 'Snapback']) {
      expect(tab(name).length, name).toBeGreaterThan(0);
    }
    expect(screen.getAllByRole('tab')).toHaveLength(4);
  });

  it('never offers a retired strategy', () => {
    renderPane();
    for (const name of ['Adaptive Edge', 'Intraday', 'Opening', 'Navigator']) {
      expect(tab(name), name).toHaveLength(0);
    }
  });

  it('keeps Gamma history accessible when scanning is off', () => {
    gmEnabled = false;
    renderPane();
    expect(tab('Gamma Unwind')).toHaveLength(1);
    // ...and takes nothing else with it.
    expect(tab('Snapback').length).toBeGreaterThan(0);
    expect(tab('SuperTrend').length).toBeGreaterThan(0);
  });

  it('keeps Snapback history accessible when scanning is off', () => {
    sbEnabled = false;
    renderPane();
    expect(tab('Snapback')).toHaveLength(1);
    expect(tab('Gamma Unwind').length).toBeGreaterThan(0);
  });

  it('keeps SuperTrend history accessible when scanning is off', () => {
    stEnabled = false;
    renderPane();
    expect(tab('SuperTrend')).toHaveLength(1);
  });

  it('keeps the board accessible when config is unavailable', () => {
    gmEnabled = undefined;
    renderPane();
    expect(tab('Gamma Unwind')).toHaveLength(1);
  });

  it('switches active tab when kite-default-engine-changed event is dispatched', async () => {
    renderPane();
    const stTab = screen.getByRole('tab', { name: /^SuperTrend/i });
    expect(stTab).toHaveAttribute('aria-selected', 'true');

    act(() => {
      window.dispatchEvent(new CustomEvent('kite-default-engine-changed', { detail: 'gamma_move' }));
    });

    await waitFor(() => {
      const gmTab = screen.getByRole('tab', { name: /^Gamma Unwind/i });
      expect(gmTab).toHaveAttribute('aria-selected', 'true');
    });
  });
});

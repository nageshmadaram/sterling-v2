import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { AdaptiveEdgeRightSidebar } from '../AdaptiveEdgeRightSidebar';
import { SterlingKiteEnginePane } from '../SterlingKiteEnginePane';

// Navigator is a peer engine with its own scan endpoint, so the one manual
// "Re-scan" control has to refresh whichever engines are actually on.
const supertrendScan = vi.fn(() => Promise.resolve());
const navigatorScan = vi.fn(() => Promise.resolve());
const gammaScan = vi.fn(() => Promise.resolve());
const snapbackScan = vi.fn(() => Promise.resolve());
const cancelSupertrend = vi.fn();
const cancelNavigator = vi.fn();

const cfg: Record<string, any> = {
  engine_enabled: true,
  trail_target: 'fast',
  exit_mode: 'one_red',
  strike_moneyness: ['ATM'],
  scan_source: 'derivatives',
  scan_expiries: ['weekly'],
  scan_expiries_indices: null,
  scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'],
  scan_stocks: [],
  scan_all_stocks: false,
  auto_execute: false,
  risk_sizing: true,
  risk_pct: 1,
  max_lots: 10,
  stop_mode: 'both',
  directional_mode: false,
  vehicle: 'otm_options',
  enabled_vehicles: ['otm_options'],
  itm_depth: 'ITM10',
  target_delta: null,
  futures_expiry: 'near',
  adx_min: null,
  atr_pct_min: null,
  wire_risk_infra: false,
};

let navigatorEnabled = true;
let scanning = false;

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: cfg }),
  useSetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({
    data: {
      generated_ms: 0, scanning, scanning_label: '', rows: [],
      next_scan_ms: 0, auto_scan: false, market_open: true,
    },
  }),
  useRunScan: () => ({ mutate: supertrendScan, mutateAsync: supertrendScan, isPending: false }),
  useCancelScan: () => ({ mutate: cancelSupertrend, isPending: false }),
}));

vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: navigatorEnabled }, revision: 1 } } }),
  // `useEngineToggles` writes Navigator under optimistic concurrency, so the
  // shared list reaches for its setter even where nothing toggles anything.
  useSetNavigatorConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useRunNavigatorScan: () => ({ mutate: navigatorScan, mutateAsync: navigatorScan, isPending: false }),
  useCancelNavigatorScan: () => ({ mutate: cancelNavigator, isPending: false }),
}));

vi.mock('../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));

// The shell reads every engine to count its tab. None of them matter here.
vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeSnapshot: () => ({ data: null }),
  // The dock hides a switched-off engine, so it asks each one whether it is on.
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true } } }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: () => ({ data: null }),
  useGammaMoveConfig: () => ({ data: { config: { enabled: true } } }),
  useGammaMoveScan: () => ({ mutate: gammaScan, mutateAsync: gammaScan, isPending: false }),
}));
vi.mock('../../../hooks/useSnapback', () => ({
  useSnapbackConfig: () => ({ data: { config: { enabled: true } } }),
  useSnapbackSnapshot: () => ({ data: undefined }),
  useRunSnapbackScan: () => ({ mutateAsync: snapbackScan, isPending: false }),
}));
// The boards themselves are not under test; stub them so the shell can mount.
vi.mock('../board/GammaMoveBoard', () => ({ GammaMoveBoard: () => <div>Gamma board</div> }));
vi.mock('../board/SnapbackBoard', () => ({ SnapbackBoard: () => <div>Snapback board</div> }));
vi.mock('../SterlingKiteEngineWithExpiry', () => ({ SterlingKiteEngineWithExpiry: () => <div /> }));

/**
 * Renders the engine-tab SHELL, not one engine's pane.
 *
 * Re-scan and the board settings used to be rendered by SuperTrend's pane, which
 * meant they existed on one tab out of five — yet re-scan already scans all five
 * and the settings drawer writes to the shared store that every board reads. They
 * moved up to the shell, so this is where they are now tested.
 *
 * The extra mocks below are the other four engines' snapshots: the shell reads
 * all of them to count each tab's live rows.
 */
function renderPane() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AdaptiveEdgeRightSidebar onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('release strategy re-scan', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    cfg.engine_enabled = true;
    navigatorEnabled = true;
    scanning = false;
  });

  it('scans SuperTrend, Gamma Unwind and Snapback in the displayed order', async () => {
    renderPane();
    fireEvent.click(screen.getByRole('button', { name: 'Re-scan SuperTrend, Gamma Unwind, Snapback' }));
    await waitFor(() => expect(snapbackScan).toHaveBeenCalledTimes(1));
    expect(supertrendScan).toHaveBeenCalledTimes(1);
    expect(gammaScan).toHaveBeenCalledTimes(1);
    expect(supertrendScan.mock.invocationCallOrder[0]).toBeLessThan(gammaScan.mock.invocationCallOrder[0]);
    expect(gammaScan.mock.invocationCallOrder[0]).toBeLessThan(snapbackScan.mock.invocationCallOrder[0]);
    expect(navigatorScan).not.toHaveBeenCalled();
  });

  it('skips SuperTrend when its engine is off and never scans retired engines', async () => {
    cfg.engine_enabled = false;
    renderPane();
    fireEvent.click(screen.getByRole('button', { name: 'Re-scan Gamma Unwind, Snapback' }));
    await waitFor(() => expect(snapbackScan).toHaveBeenCalledTimes(1));
    expect(supertrendScan).not.toHaveBeenCalled();
    expect(navigatorScan).not.toHaveBeenCalled();
  });

  it('stops the active SuperTrend scan without calling a retired endpoint', () => {
    scanning = true;
    renderPane();
    fireEvent.click(screen.getByRole('button', { name: 'Stop current scan' }));
    expect(cancelSupertrend).toHaveBeenCalledTimes(1);
    expect(cancelNavigator).not.toHaveBeenCalled();
  });
});

/**
 * The engine's own controls, which did NOT move.
 *
 * SOURCE, EXIT, VIEW and the timeframe belong to SuperTrend and change what IT
 * scans, so they stay on its own toolbar. Only re-scan and the board settings
 * went up to the shell, because those two are common to every engine.
 */
function renderEnginePane() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SterlingKiteEnginePane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('the engine controls on the search row', () => {
  beforeEach(() => {
    localStorage.clear();
    supertrendScan.mockClear();
    navigatorScan.mockClear();
    cfg.engine_enabled = true;
    navigatorEnabled = true;
    scanning = false;
  });

  it('carries the timeframe, SOURCE, EXIT and VIEW', () => {
    // These had a toolbar row of their own above the table. They now sit on the
    // search row, between the search box and COLUMNS, and that row was already
    // there — so the table gained a row of vertical space.
    renderEnginePane();
    expect(screen.getByText('1H')).toBeInTheDocument();
    expect(screen.getByText('SOURCE')).toBeInTheDocument();
    expect(screen.getByText('EXIT')).toBeInTheDocument();
    expect(screen.getByText('VIEW')).toBeInTheDocument();
  });

  it('keeps re-scan reachable with no rows at all', () => {
    // Re-scan is in the pane's TITLE BAR now, rendered by the shell, so it can no
    // longer be gated on a row count at all — which is the strongest version of
    // the property this test was written for: the one press that could fill an
    // empty table used to disappear exactly when the table was empty.
    renderPane();
    expect(screen.getByRole('button', { name: /Re-scan/ })).toBeInTheDocument();
  });
});

/**
 * The selection and the running switch are ANDed.
 *
 * A stopped engine must be skipped whatever is ticked in settings — scanning it
 * is work the operator has already declined — and an excluded engine must be
 * skipped even when it is running, which is the whole point of the setting.
 */
describe('re-scan honours both the running switch and the selection', () => {
  beforeEach(async () => {
    localStorage.clear();
    supertrendScan.mockClear();
    navigatorScan.mockClear();
    cfg.engine_enabled = true;
    navigatorEnabled = true;
    scanning = false;
  });

  it('skips a strategy the operator excluded, even though it is running', async () => {
    const { useKiteSettings } = await import('../../../store/useKiteSettings');
    useKiteSettings.getState().toggleRescanStrategy('navigator');
    renderPane();
    fireEvent.click(screen.getByRole('button', { name: /^Re-scan/ }));
    await waitFor(() => expect(supertrendScan).toHaveBeenCalledTimes(1));
    expect(navigatorScan, 'excluded from the sweep').not.toHaveBeenCalled();
    useKiteSettings.getState().toggleRescanStrategy('navigator');
  });

  it('names only what it will actually run', async () => {
    const { useKiteSettings } = await import('../../../store/useKiteSettings');
    useKiteSettings.getState().toggleRescanStrategy('navigator');
    renderPane();
    // The tooltip and the press come from one list, so they cannot disagree.
    expect(screen.queryByRole('button', { name: /Navigator,/ })).toBeNull();
    useKiteSettings.getState().toggleRescanStrategy('navigator');
  });
});

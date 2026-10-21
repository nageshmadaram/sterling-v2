import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { ConnectPane } from '../ConnectPane';
import gammaPayload from '../../__tests__/fixtures/gamma-move.json';

const render = (ui: React.ReactElement) => rtlRender(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
const setConfig = vi.fn();
const addMutate = vi.fn();

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts: [], count: 0 }, isLoading: false }),
  useAddKiteAccount: () => ({ mutate: addMutate, isPending: false, error: null }),
  useActivateKiteAccount: () => ({ mutate: vi.fn() }),
  useDeleteKiteAccount: () => ({ mutate: vi.fn() }),
  useGenerateKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useKiteBasketMargins: () => ({ mutate: vi.fn() }),
  useKiteLoginUrl: () => ({ data: undefined }),
  useKiteLogout: () => ({ mutate: vi.fn() }),
  useKiteOrderCharges: () => ({ mutate: vi.fn() }),
  useKiteOrderMargins: () => ({ mutate: vi.fn() }),
  useKiteMargins: () => ({ data: undefined }),
  useKiteStatus: () => ({ data: undefined }),
  useKiteTickerStatus: () => ({ data: undefined }),
  useKiteTickerSubscribe: () => ({ mutate: vi.fn() }),
  useKiteTickerUnsubscribe: () => ({ mutate: vi.fn() }),
  useRefreshKiteSession: () => ({ mutate: vi.fn() }),
  useTestKiteAccount: () => ({ mutate: vi.fn() }),
  useUpdateKiteAccount: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true } }),
  useSetEngineConfig: () => ({ mutate: setConfig, isPending: false }),
  usePatchEngineConfig: () => ({ mutate: setConfig, isPending: false }),
  useEngineSignals: () => ({ data: { rows: [] } }),
  // SuperTrend's contract picker moved onto this page, and it reads the live
  // Kite expiry calendar.
  useExpiryCalendar: () => ({ data: undefined, isLoading: false, isError: false, isFetching: false, refetch: vi.fn() }),
  useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
  // The shared InstrumentsGroup reads the curated stock registry. Omitted, the
  // mock throws inside the component and the whole panel renders as an empty
  // div — which reads exactly like "the settings are missing".
  useStockRegistry: () => ({ data: [{ liquidity: 'Very High', names: ['RELIANCE', 'HDFCBANK'] }] }),
}));

// The rail summarises which engines are running, so ConnectPane itself reads the
// Navigator config. Unmocked, that hook has no QueryClient and every test here fails
// before it renders anything.
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: false } } } }),
}));

vi.mock('../TradingModePanel', () => ({ TradingModePanel: () => <div>Trading mode controls</div> }));
vi.mock('../SuperTrendEnginePanel', () => ({ SuperTrendEnginePanel: () => <div>SuperTrend strategy panel</div> }));
vi.mock('../TradeRulesPanels', () => ({
  ManualRulesPanel: () => <div>Manual rules panel</div>,
  AutomaticRulesPanel: () => <div>Automatic rules panel</div>,
}));
vi.mock('../KiteTelegramPanel', () => ({
  KiteTelegramPanel: () => <div>Kite alert destinations</div>,
  BrandIconPicker: () => <div>Icon picker</div>,
}));
vi.mock('../MotionStyleSettings', () => ({ MotionStyleSettings: () => <div>Motion style choices</div> }));
// Experience now carries the signal-board order card, which asks every engine
// whether it is running. This test is about the RAIL, not about engine state.
vi.mock('../../../hooks/useEngineToggles', () => ({
  useReleaseEngineEnabled: () => ({ supertrend: true, gamma_move: true, snapback: true }),
  useReleaseEngineToggles: () => [],
}));
vi.mock('../KiteExchangeSettingsCard', () => ({ KiteExchangeSettingsCard: () => <div>Exchange choices</div> }));
vi.mock('../DailyLossLimitPanel', () => ({ DailyLossLimitPanel: () => <div>Daily loss limit</div> }));
vi.mock('../../datalake/DataLakeSettingsPanel', () => ({ DataLakeSettingsPanel: () => <div>Offline data settings</div> }));

describe('ConnectPane settings hub', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    setConfig.mockClear();
    addMutate.mockClear();
  });

  it('requires both credentials before adding an account', () => {
    render(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: '+ Add broker account' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'API key' }), { target: { value: ' test-key ' } });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'API key' }), { key: 'Enter' });
    expect(addMutate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('API secret'), { target: { value: ' test-secret ' } });
    fireEvent.keyDown(screen.getByLabelText('API secret'), { key: 'Enter' });
    expect(addMutate).toHaveBeenCalledWith(
      { label: 'My Kite', api_key: 'test-key', api_secret: 'test-secret' },
      expect.any(Object),
    );
    expect(screen.queryByText(/paper|simulation/i)).not.toBeInTheDocument();
  });

  it('uses one category rail and gives each settings family one home', () => {
    render(<ConnectPane />);

    // The page title is now the selected section's own name, under a quiet
    // "Settings" eyebrow — so the heading tracks where you are rather than
    // restating the hub on every page.
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Kite settings sections' })).toBeInTheDocument();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(screen.getByRole('heading', { name: 'Users & Broker Logins' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /SuperTrend\s*Scan, entry & exit/i }));
    expect(screen.getByRole('heading', { name: 'SuperTrend' })).toBeInTheDocument();
    expect(screen.getByText('SuperTrend strategy panel')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Notifications\s*Kite Telegram alerts/i }));
    expect(screen.getByRole('heading', { name: 'Notifications' })).toBeInTheDocument();
    expect(screen.getByText('Kite alert destinations')).toBeInTheDocument();
    expect(screen.queryByText('Icon picker')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Experience\s*Themes, fonts & motion/i }));
    expect(screen.getByRole('heading', { name: 'Experience' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Spectrum navigation settings' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Spectrum navigation layout' })).toHaveValue('cycle');
    expect(screen.getByText('Motion style choices')).toBeInTheDocument();
    expect(screen.getByText('Icon picker')).toBeInTheDocument();
  });

  it('groups the rail by what a setting decides', () => {
    render(<ConnectPane />);
    ['Connection', 'Trading', 'Signal engines', 'Platform']
      .forEach((group) => expect(screen.getByText(group)).toBeInTheDocument());
  });

  it('exposes only the three release signal engines', () => {
    render(<ConnectPane />);
    expect(screen.getByRole('button', { name: /SuperTrend/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Gamma Unwind/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Snapback/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /EMA Trend/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Value-Flow Navigator')).not.toBeInTheDocument();
    expect(screen.queryByText('Adaptive Edge')).not.toBeInTheDocument();
    expect(screen.queryByText('Intraday Pack')).not.toBeInTheDocument();
  });

  it('gives manual and automatic rules separate homes, not one filtered page', () => {
    // The single page with an All / Manual / Automatic filter made the reader
    // decode a per-row badge to know whether a rule applied to them.
    render(<ConnectPane />);

    fireEvent.click(screen.getByRole('button', { name: /Manual Trade\s*Orders you place/i }));
    expect(screen.getByRole('heading', { name: 'Manual Trade' })).toBeInTheDocument();
    expect(screen.getByText('Manual rules panel')).toBeInTheDocument();
    expect(screen.queryByText('Automatic rules panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Algo Trade\s*Orders the algo places/i }));
    expect(screen.getByRole('heading', { name: 'Algo Trade' })).toBeInTheDocument();
    expect(screen.getByText('Automatic rules panel')).toBeInTheDocument();
    expect(screen.queryByText('Manual rules panel')).not.toBeInTheDocument();
  });

  it('has no All/Manual/Automatic filter left anywhere in the rail', () => {
    render(<ConnectPane />);
    expect(screen.queryByRole('button', { name: 'All rules' })).not.toBeInTheDocument();
  });

  it('lifts the live status and manual/automatic switch out of the SuperTrend page onto their own', () => {
    // The live status and AUTO are shared controls, so they do not belong behind a single strategy page.
    render(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: /Trading (Config|Mode)/i }));
    expect(screen.getByRole('heading', { name: /Trading (Config|Mode)/i })).toBeInTheDocument();
    expect(screen.getByText('Trading mode controls')).toBeInTheDocument();
    expect(screen.getByText('Exchange choices')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /SuperTrend\s*Scan, entry & exit/i }));
    expect(screen.queryByText('Trading mode controls')).not.toBeInTheDocument();
  });

  it('sends the retired shared-market deep link to the engine that now owns its settings', () => {
    localStorage.setItem('kite_connect_tab', 'sharedScan');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'SuperTrend' })).toBeInTheDocument();
  });

  it('sends the retired order-selection deep link to Automatic Rules, which absorbed it', () => {
    localStorage.setItem('kite_connect_tab', 'orderSelection');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'Algo Trade' })).toBeInTheDocument();
  });

  it('follows the one-page Trade Rules deep link to the manual half', () => {
    localStorage.setItem('kite_connect_tab', 'rules');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'Manual Trade' })).toBeInTheDocument();
  });
});

/**
 * Gamma Unwind is reachable and carries its reference-client settings.
 *
 * ORB has had this assertion since its panel turned out to be mounted nowhere —
 * "the whole ORB UI was unreachable from the app" — and neither of these two had
 * the equivalent. Their panels are deliberately NOT mocked here: a stub that
 * renders a placeholder proves the rail wiring and nothing about whether the
 * page a person lands on actually has the controls on it.
 */
describe('ConnectPane — every option engine is reachable and complete', () => {
  // These panels are real, so they need a QueryClient and the config payloads
  // the app serves them. Anything less and the test proves the rail wiring only.
  const PAYLOADS: Record<string, unknown> = {
    '/api/v1/config/gamma-move': gammaPayload,
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      const key = Object.keys(PAYLOADS).find((k) => url.endsWith(k));
      return {
        ok: true, status: 200,
        json: async () => (key ? PAYLOADS[key] : {}),
        text: async () => JSON.stringify(key ? PAYLOADS[key] : {}),
        headers: new Headers({ 'content-type': 'application/json' }),
      } as Response;
    }));
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  const show = (ui: React.ReactElement) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
  };

  const ENGINES: Array<[string, RegExp, string]> = [
    ['Gamma Unwind', /Gamma Unwind\s*Zones, E2, E1 & gates/i, 'Gamma Unwind'],
  ];

  it.each(ENGINES)('gives %s a home in the rail', (_label, railName, heading) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
  });

  it.each(ENGINES)('%s carries the uploaded-client watchlist contract', async (_label, railName) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
    const text = document.body.textContent ?? '';
    expect(text).toContain('Expiry switch threshold');
    expect(text).toContain('effective HOI-1');
    expect(text).toContain('1m base');
    expect(text).toContain('5m signal');
    expect(text).toContain('15m display');
  });

  it.each(ENGINES)('%s opens Watchlist and strike', async (label, railName) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
    const summary = [...document.querySelectorAll('summary')]
      .find((el) => /Watchlist and strike/.test(el.textContent ?? ''));
    expect(summary, `${label} has no Watchlist and strike section`).toBeTruthy();
    expect((summary!.closest('details') as HTMLDetailsElement).open,
           `${label}'s Watchlist and strike section is collapsed`).toBe(true);
  });
});

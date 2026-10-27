vi.mock('../../../hooks/useConfigurationCapabilities',async original=>({...await original(),useConfigurationCapabilities:()=>({data:{active:true,administrator:true,sections:{brokers:{visible:true,grants:[{action:'create',resource_id:'own',expires_at_ms:null}]},telegram:{visible:true,grants:[{action:'create',resource_id:'own',expires_at_ms:null}]},integrations:{visible:true,grants:[{action:'create',resource_id:'own',expires_at_ms:null}]}}}})}));
/**
 * What the server refuses in the Simulation view must not be on screen there as a live button that
 * ends in a 403. Each of these surfaces shows a one-line reason in its place, and in the LIVE view
 * is exactly what it was.
 *
 * Refused by the server in the Simulation view: the kill switch, safe-mode resume and other
 * operator writes, broker login/logout/accounts, TrueData, alert destinations and the live daily
 * loss limit (app/core/auth.py SIM_VIEW_DENY_RULES).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts: [], count: 0 }, isLoading: false }),
  useAddKiteAccount: () => ({ mutate: vi.fn(), isPending: false, error: null }),
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
  useSetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({ data: { rows: [] } }),
  useExpiryCalendar: () => ({ data: undefined, isLoading: false, isError: false, isFetching: false, refetch: vi.fn() }),
  useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
  useStockRegistry: () => ({ data: [] }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: false } } } }),
}));
vi.mock('../../../hooks/useEngineToggles', () => ({
  useReleaseEngineEnabled: () => ({ supertrend: true, gamma_move: true, snapback: true }),
  useReleaseEngineToggles: () => [],
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
vi.mock('../KiteExchangeSettingsCard', () => ({ KiteExchangeSettingsCard: () => <div>Exchange choices</div> }));
vi.mock('../DailyLossLimitPanel', () => ({ DailyLossLimitPanel: () => <div>Daily loss limit editor</div> }));
vi.mock('../../datalake/DataLakeSettingsPanel', () => ({ DataLakeSettingsPanel: () => <div>Offline data settings</div> }));
vi.mock('../../truedata/TrueDataCredentialsPanel', () => ({
  TrueDataCredentialsPanel: () => <div>TrueData credentials</div>,
  DataSourceSelector: () => <div>Data source selector</div>,
}));

// Operations screen
vi.mock('../../../hooks/useLiveOperations', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useLiveOperations')>('../../../hooks/useLiveOperations');
  return {
    ...actual,
    useLiveOperations: () => ({ isError: false, data: { production_mode: 'LIVE', generated_at: new Date().toISOString(), operator: { resume_available: true }, safety: {}, deployment: {}, security: {}, broker: {} } }),
    useOperatorTradeHalt: () => ({ stop: { mutate: vi.fn(), isPending: false }, resume: { mutate: vi.fn(), isPending: false } }),
  };
});
vi.mock('../ops/ModeBanner', () => ({ ModeBanner: () => <div>Mode banner</div> }));
vi.mock('../ops/SafetyAdmissionCard', () => ({ SafetyAdmissionCard: () => <div>Safety card</div> }));
vi.mock('../ops/DeploymentCard', () => ({ DeploymentCard: () => <div>Deployment card</div> }));
vi.mock('../ops/BrokerExposureCard', () => ({ BrokerExposureCard: () => <div>Broker card</div> }));
vi.mock('../ops/OperatorActions', () => ({ OperatorActions: () => <button type="button">STOP ALL NEW TRADES</button> }));

import { ConnectPane } from '../ConnectPane';
import { LiveOperationsPanel } from '../LiveOperationsPanel';
import { useViewStore } from '../../../store/useViewStore';

const wrap = (ui: React.ReactElement) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);
const openSection = (name: RegExp) => fireEvent.click(screen.getByRole('button', { name }));

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); useViewStore.setState({ view: 'live' }); });
afterEach(() => { cleanup(); useViewStore.setState({ view: 'live' }); });

describe('Settings in the live view', () => {
  it('still offers broker accounts, the data credentials, the loss limit and Telegram', () => {
    render(wrap(<ConnectPane />));
    expect(screen.getByRole('button', { name: '+ Add broker account' })).toBeInTheDocument();
    openSection(/^TrueData Feed$/i);
    expect(screen.getByText('TrueData credentials')).toBeInTheDocument();
    expect(screen.queryByTestId('sim-denied-note')).toBeNull();
  });

  it('shows the loss limit editor and the alert destinations', () => {
    render(wrap(<ConnectPane />));
    openSection(/Trading (Config|Mode)/i);
    expect(screen.getByText('Daily loss limit editor')).toBeInTheDocument();
    openSection(/^Notifications$/i);
    expect(screen.getByText('Kite alert destinations')).toBeInTheDocument();
  });
});

describe('Settings in the Simulation view', () => {
  beforeEach(() => { useViewStore.setState({ view: 'simulation' }); });

  it('replaces broker login, logout and accounts with a reason', () => {
    render(wrap(<ConnectPane />));
    expect(screen.queryByRole('button', { name: '+ Add broker account' })).toBeNull();
    const note = screen.getByTestId('sim-denied-note');
    expect(note).toHaveTextContent('Broker login, logout and accounts');
    expect(note).toHaveTextContent('Simulation');
    expect(note).toHaveTextContent(/never signs in to Zerodha/);
  });

  it('replaces the live daily loss limit with the simulation\'s own, explained', () => {
    render(wrap(<ConnectPane />));
    openSection(/Trading (Config|Mode)/i);
    expect(screen.queryByText('Daily loss limit editor')).toBeNull();
    const note = screen.getByTestId('sim-denied-note');
    expect(note).toHaveTextContent('The live daily loss limit is not available in the Simulation view');
    expect(note).toHaveTextContent(/2% loss and warns at 1%/);
    // The rest of the page is unaffected.
    expect(screen.getByText('Trading mode controls')).toBeInTheDocument();
    expect(screen.getByText('Exchange choices')).toBeInTheDocument();
  });

  it('replaces the market-data credentials with a reason', () => {
    render(wrap(<ConnectPane />));
    openSection(/^TrueData Feed$/i);
    expect(screen.queryByText('TrueData credentials')).toBeNull();
    expect(screen.getByTestId('sim-denied-note')).toHaveTextContent('Market-data credentials is not available in the Simulation view');
  });

  it('replaces Telegram alert destinations with a reason', () => {
    render(wrap(<ConnectPane />));
    openSection(/^Notifications$/i);
    expect(screen.queryByText('Kite alert destinations')).toBeNull();
    expect(screen.getByTestId('sim-denied-note')).toHaveTextContent('Telegram alerts is not available in the Simulation view');
  });

  it('keeps settings that belong to the simulation itself', () => {
    render(wrap(<ConnectPane />));
    openSection(/^SuperTrend$/i);
    expect(screen.getByText('SuperTrend strategy panel')).toBeInTheDocument();
    expect(screen.queryByTestId('sim-denied-note')).toBeNull();
  });
});

describe('Operations screen', () => {
  it('offers STOP ALL NEW TRADES in the live view', () => {
    render(wrap(<LiveOperationsPanel />));
    expect(screen.getByRole('button', { name: 'STOP ALL NEW TRADES' })).toBeInTheDocument();
    expect(screen.queryByTestId('sim-denied-note')).toBeNull();
  });

  it('does not in the Simulation view, and says why', () => {
    useViewStore.setState({ view: 'simulation' });
    render(wrap(<LiveOperationsPanel />));
    expect(screen.queryByRole('button', { name: 'STOP ALL NEW TRADES' })).toBeNull();
    expect(screen.getByTestId('sim-denied-note')).toHaveTextContent('Stopping and resuming new trades is not available in the Simulation view');
    expect(screen.getByText('Safety card')).toBeInTheDocument();     // the read-only cards stay
  });
});

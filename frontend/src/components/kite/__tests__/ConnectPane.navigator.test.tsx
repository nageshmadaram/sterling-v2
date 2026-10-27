import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { ConnectPane } from '../ConnectPane';

vi.mock('../../../hooks/useConfigurationCapabilities', async importOriginal => {
  const actual = await importOriginal<typeof import('../../../hooks/useConfigurationCapabilities')>();
  return { ...actual, useConfigurationCapabilities: () => ({ isError: false, data: {
    user_id: 'default', active: true, administrator: true, revision: 0,
    sections: Object.fromEntries(['brokers', 'telegram', 'integrations'].map(section => [section, {
      visible: true, grants: ['create', 'update', 'test', 'recipients'].map(action => ({ action, resource_id: 'own', expires_at_ms: null })),
    }])),
  } }) };
});


vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts: [], count: 0 }, isLoading: false }),
  useAddKiteAccount: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useActivateKiteAccount: () => ({ mutate: vi.fn() }),
  useDeleteKiteAccount: () => ({ mutate: vi.fn() }),
  useGenerateKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useKiteBasketMargins: () => ({ mutate: vi.fn() }),
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
}));

vi.mock('../SterlingUsersPanel', () => ({ SterlingUsersPanel: () => <div>Sterling user management</div> }));
vi.mock('../PriceRulesPanel', () => ({ PriceRulesPanel: () => <div>Price Rules trading plans</div> }));

vi.mock('../TradingModePanel', () => ({ TradingModePanel: () => <div>Trading config</div> }));
vi.mock('../TradeRulesPanels', () => ({
  ManualRulesPanel: () => <div>Manual rules panel</div>,
  AutomaticRulesPanel: () => <div>Automatic rules panel</div>,
}));
vi.mock('../SuperTrendEnginePanel', () => ({ SuperTrendEnginePanel: () => <div>SuperTrend strategy panel</div> }));
vi.mock('../../datalake/DataLakeSettingsPanel', () => ({ DataLakeSettingsPanel: () => <div>Offline data settings</div> }));
vi.mock('../KiteTelegramPanel', () => ({
  KiteTelegramPanel: () => <div>Kite alert destinations</div>,
  BrandIconPicker: () => <div>Icon picker</div>,
}));
vi.mock('../MotionStyleSettings', () => ({ MotionStyleSettings: () => <div>Motion style choices</div> }));
vi.mock('../KiteExchangeSettingsCard', () => ({ KiteExchangeSettingsCard: () => <div>Exchange choices</div> }));

describe('ConnectPane — release strategy scope', () => {
  it('links to independent administration from Connect', () => {
    render(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: /^Broker accounts/i }));
    expect(screen.getByRole('link', { name: 'Open Administration' })).toHaveAttribute('href', '/administration');
  });
  it('opens Price Rules using the mobile section picker', () => {
    render(<ConnectPane />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Settings section' }), { target: { value: 'priceRules' } });
    expect(screen.getByText('Price Rules trading plans')).toBeInTheDocument();
  });
  it('does not expose retired strategy settings in the release rail', () => {
    render(<ConnectPane />);
    expect(screen.queryByText('Value-Flow Navigator')).not.toBeInTheDocument();
    expect(screen.queryByText('Adaptive Edge')).not.toBeInTheDocument();
    expect(screen.queryByText('Intraday Pack')).not.toBeInTheDocument();
  });

  it('keeps the four release strategies visible', () => {
    render(<ConnectPane />);
    expect(screen.getByRole('button', { name: /SuperTrend/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Gamma Unwind/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Snapback/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Price Rules/i }));
    expect(screen.getByText('Price Rules trading plans')).toBeInTheDocument();
  });
});

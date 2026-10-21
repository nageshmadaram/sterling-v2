/**
 * Every release engine's running switch, and the dock following it.
 *
 * "What is running" lists SuperTrend, Gamma Unwind and Snapback, each with its own
 * power switch. Both this panel and the dock read from `useReleaseEngineToggles` /
 * `useReleaseEngineEnabled`, so they cannot drift apart. Retired strategies have no
 * switch here.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const patchSuperTrend = vi.fn();
const updateGamma = vi.fn();
const togglePriceRules = vi.fn();
let priceRulesEnabled = false;
let priceRulesAvailable = true;
let gammaConfig: { enabled: boolean } | null = { enabled: true };
let snapbackConfig: { enabled: boolean } | null = { enabled: true };

vi.mock('../../../hooks/useEmaTrend', () => ({
  useEmaTrendConfig: () => ({ data: { revision: 7, config: { enabled: false, auto_execute: false } } }),
  useUpdateEmaTrend: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true, auto_execute: false } }),
  usePatchEngineConfig: () => ({ mutate: patchSuperTrend, isPending: false }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: gammaConfig ? { config: gammaConfig } : undefined }),
  useUpdateGammaMove: () => ({ mutate: updateGamma, isPending: false }),
}));
vi.mock('../../../hooks/useSnapback', () => ({
  useSnapbackConfig: () => ({ data: snapbackConfig ? { config: snapbackConfig } : undefined }),
  useUpdateSnapback: () => ({ mutate: vi.fn(), isPending: false }),
  useSnapbackSnapshot: () => ({ data: undefined }),
}));
vi.mock('../../../hooks/usePriceRulesSwitch', () => ({
  usePriceRulesSwitch: () => ({ enabled: priceRulesEnabled, pending: false,
    toggle: priceRulesAvailable ? togglePriceRules : null, error: null }),
}));
vi.mock('../TradingModeControls', () => ({ TradingModeControls: () => <div>mode controls</div> }));
vi.mock('../../truedata/TrueDataCredentialsPanel', () => ({ DataSourceSelector: () => <div>data source selector</div> }));

import { TradingModePanel } from '../TradingModePanel';

const ENGINES = ['SuperTrend engine', 'Gamma Unwind', 'Snapback', 'EMA Trend · paper', 'Price Rules'];
const RETIRED = ['Value-Flow Navigator', 'Adaptive Edge', 'Intraday pack', 'Opening Volume'];

/**
 * By ROLE, not by text.
 *
 * These engine names also appear in the "Included in re-scan" list further down
 * the same panel, so `getByText` finds two of several and throws. The running
 * control is a `role="switch"` carrying the engine's name as its `aria-label`;
 * the re-scan rows are plain checkboxes. The role separates them exactly.
 */
function switchFor(label: string): HTMLElement {
  return screen.getByRole('switch', { name: label });
}

beforeEach(() => {
  vi.clearAllMocks();
  gammaConfig = { enabled: true };
  snapbackConfig = { enabled: true };
  priceRulesEnabled = false;
  priceRulesAvailable = true;
});
afterEach(cleanup);

describe('What is running', () => {
  it('names every release engine', () => {
    render(<TradingModePanel />);
    for (const label of ENGINES) expect(switchFor(label)).toBeInTheDocument();
  });

  it('offers no switch for a retired strategy', () => {
    render(<TradingModePanel />);
    for (const label of RETIRED) expect(screen.queryByRole('switch', { name: label })).toBeNull();
    expect(screen.getAllByRole('switch')).toHaveLength(ENGINES.length);
  });

  it('shows Price Rules OFF by default and allows an ON request', () => {
    render(<TradingModePanel />);
    expect(switchFor('Price Rules')).not.toBeChecked();
    fireEvent.click(switchFor('Price Rules'));
    expect(togglePriceRules).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Open Price Rules plans and owned positions' })).toBeInTheDocument();
  });

  it('disables the switch if Price Rules state is unknown', () => {
    priceRulesAvailable = false;
    render(<TradingModePanel />);
    expect(switchFor('Price Rules')).toBeDisabled();
  });

  it('switches SuperTrend off through its own field name', () => {
    render(<TradingModePanel />);
    fireEvent.click(switchFor('SuperTrend engine'));
    // `engine_enabled`, not `enabled` — this engine predates the convention.
    expect(patchSuperTrend).toHaveBeenCalledWith({ engine_enabled: false });
  });

  it('switches Gamma Unwind off with the plain enabled field', () => {
    render(<TradingModePanel />);
    fireEvent.click(switchFor('Gamma Unwind'));
    expect(updateGamma).toHaveBeenCalledWith({ enabled: false });
  });

  it('disables the switch for an engine whose config has not arrived', () => {
    // A toggle sends the opposite of the current value, so with no current value
    // there is nothing honest to send. Disabled beats guessing.
    gammaConfig = null;
    render(<TradingModePanel />);
    expect(switchFor('Gamma Unwind')).toBeDisabled();
  });

  it('fails closed while a config is unavailable', () => {
    gammaConfig = null;
    render(<TradingModePanel />);
    // UNKNOWN is not ON. The switch is also disabled, so the operator cannot
    // mutate a state the UI never successfully read.
    expect(switchFor('Gamma Unwind')).not.toBeChecked();
    expect(switchFor('Gamma Unwind')).toBeDisabled();
  });

  it('keeps Snapback OFF while its config is unavailable', () => {
    snapbackConfig = null;
    render(<TradingModePanel />);
    expect(switchFor('Snapback')).not.toBeChecked();
    expect(switchFor('Snapback')).toBeDisabled();
  });
});

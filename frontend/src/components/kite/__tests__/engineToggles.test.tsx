/**
 * Every engine's running switch, and the dock following it.
 *
 * "What is running" answered for two engines out of six: SuperTrend had a switch,
 * Navigator had a "Configure →" link, and ORB, Gamma Unwind, Adaptive Edge and ATM
 * had nothing — the only way to stop one was to find its own settings page. And a
 * stopped engine still got a dock tab, on the reasoning that a missing tab is
 * harder to read than a stopped one.
 *
 * Both surfaces now come from `useEngineToggles` / `useEngineEnabled`, so they
 * cannot drift apart the way this repo's engine lists keep doing.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const setNavigator = vi.fn();
const patchSuperTrend = vi.fn();
let navRecord: unknown = { config: { enabled: true, auto_execute_originated: false }, revision: 3 };
let snapbackConfig: { enabled: boolean } | null = { enabled: true };

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true, auto_execute: false } }),
  usePatchEngineConfig: () => ({ mutate: patchSuperTrend, isPending: false }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: navRecord ? { record: navRecord } : undefined }),
  useSetNavigatorConfig: () => ({ mutate: setNavigator, isPending: false }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: { config: { enabled: true } } }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useIntraday', () => ({
  useIntradayConfig: () => ({ data: { config: { enabled: true } } }),
  useUpdateIntraday: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useSnapback', () => ({
  useSnapbackConfig: () => ({ data: snapbackConfig ? { config: snapbackConfig } : undefined }),
  useUpdateSnapback: () => ({ mutate: vi.fn(), isPending: false }),
  useSnapbackSnapshot: () => ({ data: undefined }),
}));
vi.mock('../TradingModeControls', () => ({ TradingModeControls: () => <div>mode controls</div> }));
vi.mock('../../truedata/TrueDataCredentialsPanel', () => ({ DataSourceSelector: () => <div>data source selector</div> }));

import { TradingModePanel } from '../TradingModePanel';

const ENGINES = [
  'SuperTrend engine', 'Gamma Unwind', 'Snapback',
];

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
  navRecord = { config: { enabled: true, auto_execute_originated: false }, revision: 3 };
  snapbackConfig = { enabled: true };
});
afterEach(cleanup);

describe('What is running', () => {
  it('names every engine', () => {
    render(<TradingModePanel />);
    for (const label of ENGINES) expect(switchFor(label)).toBeInTheDocument();
  });

  it('offers a switch for each of them', () => {
    render(<TradingModePanel />);
    for (const label of ENGINES) expect(switchFor(label)).toBeInTheDocument();
  });

  it('no longer sends the operator elsewhere to stop Navigator', () => {
    render(<TradingModePanel />);
    expect(screen.queryByRole('button', { name: /Configure/i })).toBeNull();
  });

  it('switches SuperTrend off through its own field name', () => {
    render(<TradingModePanel />);
    fireEvent.click(switchFor('SuperTrend engine'));
    // `engine_enabled`, not `enabled` — this engine predates the convention.
    expect(patchSuperTrend).toHaveBeenCalledWith({ engine_enabled: false });
  });

  it('does not expose retired engines even when their configs remain enabled', () => {
    render(<TradingModePanel />);
    for (const name of ['Value-Flow Navigator', 'Adaptive Edge', 'Intraday pack']) {
      expect(screen.queryByRole('switch', { name })).toBeNull();
    }
  });

  it('keeps unpromoted Snapback OFF while its config is unavailable', () => {
    snapbackConfig = null;
    render(<TradingModePanel />);
    expect(switchFor('Snapback')).not.toBeChecked();
    expect(switchFor('Snapback')).toBeDisabled();
  });
});

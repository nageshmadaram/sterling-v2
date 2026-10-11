/**
 * The trading controls in both views, with the real per-strategy mode selector.
 *
 * Live view: MANUAL | SIMULATION | AUTO per strategy sits in the controls.
 * Simulation view: the same card acts on the virtual tenant, shows the modes read-only, and does
 * not offer what the server refuses there (kill switch, safe-mode resume) - it says why instead.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';

const haltMutate = vi.fn();
const squareOffMutate = vi.fn();
const resumeMutate = vi.fn();
const patchMutate = vi.fn();
let liveResult: any = { available: false, status: null };

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts: [{ id: 'A', is_active: true, connected: true, has_credentials: true, label: 'Main' }] } }),
  useKiteStatus: () => ({ data: { connected: true, account_id: 'A' } }),
}));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { auto_execute: false, vehicle: 'otm_options', target_delta: 0.5 } }),
  usePatchEngineConfig: () => ({ mutate: patchMutate, isPending: false }),
  useEngineReadiness: () => ({ data: { ready_for_live: true, blockers: [] }, refetch: vi.fn(async () => ({ data: { ready_for_live: true, blockers: [] } })) }),
  useApplyProductionConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEmergencySquareOff: () => ({ mutate: squareOffMutate, isPending: false }),
  useEmergencyHalt: () => ({ mutate: haltMutate, isPending: false }),
}));
vi.mock('../../../hooks/useLiveStatus', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useLiveStatus')>('../../../hooks/useLiveStatus');
  return { ...actual, useLiveStatus: () => ({ data: liveResult }), useResumeSafeMode: () => ({ mutate: resumeMutate, isPending: false }) };
});
vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

const get = vi.fn();
const put = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), put: (...a: unknown[]) => put(...a), post: vi.fn() } }));

import { TradingModeControls } from '../TradingModeControls';
import { normalizeLiveStatus } from '../../../hooks/useLiveStatus';
import { useViewStore } from '../../../store/useViewStore';
import { makeStatus, strategies } from '../../sim/__tests__/simTestUtils';

function renderControls() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  return render(<QueryClientProvider client={client}><TradingModeControls /></QueryClientProvider>);
}

const safeModeOn = () => ({
  available: true,
  status: normalizeLiveStatus({
    safe_mode: { state: 'SAFE_MODE', reason: 'Position mismatch', triggers: ['POSITION_MISMATCH'] },
    live_execution: true, recovery_state: 'NORMAL', exposure_observation: { fresh: true, age_s: 3 },
  }),
});

beforeEach(() => {
  [haltMutate, squareOffMutate, resumeMutate, patchMutate, get, put].forEach((m) => m.mockReset());
  liveResult = { available: false, status: null };
  useViewStore.setState({ view: 'live' });
  get.mockResolvedValue(makeStatus({ strategies: strategies({ gamma: 'SIMULATION' }) }));
});
afterEach(() => { cleanup(); useViewStore.setState({ view: 'live' }); });

describe('live view', () => {
  it('has the per-strategy MANUAL | SIMULATION | AUTO selector, bound to the status modes', async () => {
    renderControls();
    const modes = screen.getByTestId('strategy-modes');
    for (const name of ['Supertrend mode', 'Snapback mode', 'Gamma Unwind mode']) {
      expect(within(modes).getByRole('group', { name })).toBeInTheDocument();
    }
    await waitFor(() => expect(within(within(modes).getByRole('group', { name: 'Gamma Unwind mode' })).getByRole('button', { name: 'SIMULATION' }))
      .toHaveAttribute('aria-pressed', 'true'));
    expect(get).toHaveBeenCalledWith('/api/v1/sim/status');
  });

  it('puts a strategy into Simulation only after the virtual-money confirmation', async () => {
    put.mockResolvedValue({ strategy: 'supertrend', mode: 'SIMULATION', strategies: strategies({ supertrend: 'SIMULATION' }) });
    renderControls();
    const group = within(screen.getByTestId('strategy-modes')).getByRole('group', { name: 'Supertrend mode' });
    await waitFor(() => expect(within(group).getByRole('button', { name: 'SIMULATION' })).toBeEnabled());
    fireEvent.click(within(group).getByRole('button', { name: 'SIMULATION' }));
    expect(put).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toHaveTextContent(/virtual money/);
    fireEvent.click(screen.getByRole('button', { name: 'Start Simulation' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/sim/strategy/supertrend/mode', { mode: 'SIMULATION' }));
    // Nothing else was touched: the engine's own config is not written from here.
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('still offers the kill switch and the safe-mode resume', () => {
    liveResult = safeModeOn();
    renderControls();
    expect(screen.getByRole('button', { name: 'Kill Switch & Halt' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume trading' })).toBeInTheDocument();
    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(screen.queryByTestId('sim-denied-note')).toBeNull();
  });
});

describe('Simulation view', () => {
  beforeEach(() => { useViewStore.setState({ view: 'simulation' }); });

  it('is labelled SIMULATION, not LIVE', () => {
    renderControls();
    expect(screen.getByText('SIMULATION', { selector: 'span' })).toBeInTheDocument();
    expect(screen.queryByText('LIVE')).toBeNull();
  });

  it('does not offer the kill switch, and says why', () => {
    renderControls();
    expect(screen.queryByRole('button', { name: 'Kill Switch & Halt' })).toBeNull();
    const note = screen.getByTestId('sim-denied-note');
    expect(note).toHaveTextContent('Kill Switch & Halt is not available in the Simulation view');
    expect(note).toHaveTextContent(/real account/);
    expect(haltMutate).not.toHaveBeenCalled();
  });

  it('does not offer safe-mode resume, and says why', () => {
    liveResult = safeModeOn();
    renderControls();
    expect(screen.queryByRole('button', { name: 'Resume trading' })).toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('Safe mode ON');
    expect(screen.getAllByTestId('sim-denied-note').map((n) => n.textContent).join(' '))
      .toContain('Resuming trading is not available in the Simulation view');
  });

  it('keeps Square-Off All, worded for virtual money', () => {
    renderControls();
    fireEvent.click(screen.getByRole('button', { name: 'Square-Off All' }));
    expect(screen.getByRole('dialog')).toHaveTextContent(/Virtual money only/);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Square-Off' }));
    expect(squareOffMutate).toHaveBeenCalledTimes(1);
  });

  it('shows the strategy modes read-only', async () => {
    renderControls();
    const modes = screen.getByTestId('strategy-modes');
    await waitFor(() => expect(within(modes).getByTestId('mode-badge-gamma')).toHaveTextContent('SIMULATION'));
    expect(within(modes).queryByRole('button')).toBeNull();
  });

  it('words the shared AUTO confirmation as virtual money', () => {
    renderControls();
    const shared = screen.getByText('AUTOMATIC ENTRY').parentElement!.parentElement!;
    fireEvent.click(within(shared).getByRole('button', { name: 'ALGO' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/virtual account/);
    expect(dialog).toHaveTextContent(/no real orders/);
    expect(dialog).not.toHaveTextContent(/your Zerodha account/);
  });

  it('a Go to LIVE button on a note takes the operator back', () => {
    renderControls();
    fireEvent.click(within(screen.getByTestId('sim-denied-note')).getByRole('button', { name: 'Go to LIVE' }));
    expect(useViewStore.getState().view).toBe('live');
  });
});

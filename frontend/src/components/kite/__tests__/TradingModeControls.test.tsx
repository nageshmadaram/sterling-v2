/**
 * Trading controls are live-only: one LIVE chip, the AUTO arm switch, the live safety
 * status and the Safeguards panel.
 *
 * Rendering must never place an order or change a trading mode. That rule was written
 * after both confirm handlers were left as unbraced arrow functions: the statement that
 * followed the first semicolon ran on every render, so opening this panel armed AUTO
 * with no click and each success re-rendered and did it again.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';

const patchMutate = vi.fn();
const refetchReadiness = vi.fn();
const resumeMutate = vi.fn();
const squareOffMutate = vi.fn();
const haltMutate = vi.fn();
const presetMutate = vi.fn();

let accounts: any[] = [];
let kiteStatus: any = { connected: true, account_id: 'LIVE-1' };
let autoExecute = false;
let readinessBlockers: string[] = [];
let liveResult: any = { available: false, status: null };
const readyReadiness = () => ({
  ready_for_live: readinessBlockers.length === 0,
  blockers: readinessBlockers,
  warnings: [],
  checks: {},
  is_live_account: true,
  account_label: 'Live',
  timestamp_ms: 1,
});

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts } }),
  useKiteStatus: () => ({ data: kiteStatus }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { auto_execute: autoExecute, vehicle: 'otm_options', target_delta: 0.5 } }),
  usePatchEngineConfig: () => ({ mutate: patchMutate, isPending: false }),
  useEngineReadiness: () => ({
    data: readyReadiness(),
    refetch: refetchReadiness,
  }),
  useApplyProductionConfig: () => ({ mutate: presetMutate, isPending: false }),
  useEmergencySquareOff: () => ({ mutate: squareOffMutate, isPending: false }),
  useEmergencyHalt: () => ({ mutate: haltMutate, isPending: false }),
}));

vi.mock('../../../hooks/useLiveStatus', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useLiveStatus')>('../../../hooks/useLiveStatus');
  return {
    ...actual,
    useLiveStatus: () => ({ data: liveResult }),
    useResumeSafeMode: () => ({ mutate: resumeMutate, isPending: false }),
  };
});

vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

// The per-strategy MANUAL | SIMULATION | AUTO selector has its own tests (components/sim and
// TradingModeControls.simulation.test.tsx); here it would only duplicate the AUTO and MANUAL labels.
vi.mock('../../sim/StrategyModeSelector', () => ({
  StrategyModeSelector: () => <div data-testid="strategy-modes-stub" />,
}));

import { TradingModeControls } from '../TradingModeControls';
import { normalizeLiveStatus } from '../../../hooks/useLiveStatus';

const LIVE_ACCOUNT = {
  id: 'LIVE-1', is_active: true, is_paper: false, connected: true, has_credentials: true, label: 'Main',
};

function available(over: Record<string, unknown> = {}) {
  return {
    available: true,
    status: normalizeLiveStatus({
      safe_mode: { state: 'NORMAL', reason: null, triggers: [] },
      live_execution: true,
      recovery_state: 'NORMAL',
      exposure_observation: { fresh: true, age_s: 4 },
      ...over,
    }),
  };
}

beforeEach(() => {
  [patchMutate, refetchReadiness, resumeMutate, squareOffMutate, haltMutate, presetMutate].forEach((m) => m.mockReset());
  accounts = [LIVE_ACCOUNT];
  kiteStatus = { connected: true, account_id: 'LIVE-1' };
  autoExecute = false;
  readinessBlockers = [];
  liveResult = { available: false, status: null };
  refetchReadiness.mockImplementation(async () => ({ data: readyReadiness() }));
});
afterEach(cleanup);

describe('TradingModeControls is live-only', () => {
  it('shows a LIVE chip and no broker-mode toggle', () => {
    render(<TradingModeControls />);

    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(screen.queryByText('SIMULATION')).toBeNull();      // no SIMULATION chip in the live view
    expect(screen.queryByText(/paper/i)).toBeNull();
    expect(screen.queryByText(/BROKER MODE/i)).toBeNull();
    expect(screen.getByText('MANUAL')).toBeInTheDocument();
    expect(screen.getByText('AUTO')).toBeInTheDocument();
  });

  it('names the active account and the session state', () => {
    render(<TradingModeControls />);
    expect(screen.getByText(/TRADING CONFIG · Main/)).toBeInTheDocument();
    expect(screen.getByText('Session active')).toBeInTheDocument();
  });

  it('says login is required when the broker session is gone', () => {
    kiteStatus = { connected: false, account_id: 'LIVE-1' };
    render(<TradingModeControls />);
    expect(screen.getByText('Login required')).toBeInTheDocument();
  });

  it('does not call a transient broker failure a logout', () => {
    kiteStatus = { connected: false, account_id: 'LIVE-1', transient: true };
    render(<TradingModeControls />);
    expect(screen.getByText('Broker unreachable')).toBeInTheDocument();
    expect(screen.queryByText('Login required')).toBeNull();
  });

  it('still offers Safeguards when no account is active', () => {
    accounts = [];
    kiteStatus = undefined;
    render(<TradingModeControls />);
    expect(screen.getByText('No account')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Square-Off All' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Kill Switch & Halt' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Load Production Preset/ })).toBeInTheDocument();
  });

  it('renders and re-renders without changing anything', () => {
    const { rerender } = render(<TradingModeControls />);
    rerender(<TradingModeControls />);
    rerender(<TradingModeControls />);
    expect(patchMutate).not.toHaveBeenCalled();
    expect(squareOffMutate).not.toHaveBeenCalled();
    expect(haltMutate).not.toHaveBeenCalled();
    expect(presetMutate).not.toHaveBeenCalled();
    expect(resumeMutate).not.toHaveBeenCalled();
  });
});

describe('arming AUTO', () => {
  it('needs a click and then a confirmation', async () => {
    render(<TradingModeControls />);

    fireEvent.click(screen.getByText('AUTO'));
    expect(patchMutate).not.toHaveBeenCalled();
    expect(refetchReadiness).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(patchMutate).toHaveBeenCalledTimes(1));
    expect(patchMutate.mock.calls[0][0]).toEqual({ auto_execute: true });
  });

  it('rechecks readiness when confirming, not only when the dialog opens', async () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(patchMutate).toHaveBeenCalledWith({ auto_execute: true }, expect.any(Object)));
    expect(refetchReadiness).toHaveBeenCalledTimes(2);
  });

  it('refuses a readiness failure that occurs while the confirmation is open', async () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockResolvedValue({
      data: { ...readyReadiness(), ready_for_live: false, blockers: ['Broker session is not connected.'] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('refuses cached readiness when its refresh reports an error', async () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockResolvedValue({ data: readyReadiness(), isError: true });
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('handles a rejected readiness refresh without arming', async () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockRejectedValue(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('is refused while readiness blockers exist, and lists them', () => {
    readinessBlockers = ['Daily loss limit is not configured.'];
    render(<TradingModeControls />);

    fireEvent.click(screen.getByText('AUTO'));

    expect(screen.getByRole('button', { name: 'Blockers Exist' })).toBeDisabled();
    expect(within(screen.getByRole('dialog')).getByText('Daily loss limit is not configured.')).toBeInTheDocument();
  });

  it('is refused while safe mode is on or the kill switch is off', () => {
    liveResult = available({
      safe_mode: { state: 'SAFE_MODE', reason: 'Unknown broker exposure', triggers: ['UNKNOWN_BROKER_EXPOSURE'] },
      live_execution: false,
    });
    render(<TradingModeControls />);

    fireEvent.click(screen.getByText('AUTO'));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Blockers Exist' })).toBeDisabled();
    expect(within(dialog).getByText(/Safe mode is ON/)).toBeInTheDocument();
    expect(within(dialog).getByText(/kill switch/i)).toBeInTheDocument();
  });

  it('is refused while recovery is pending', () => {
    liveResult = available({ recovery_state: 'RECOVERY_REQUIRED' });
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    expect(within(screen.getByRole('dialog')).getByText(/Recovery: recovery required/)).toBeInTheDocument();
  });

  it('disarms with one click', () => {
    autoExecute = true;
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('MANUAL'));
    expect(patchMutate.mock.calls[0][0]).toEqual({ auto_execute: false });
  });

  it('warns when AUTO is armed but entries are blocked', () => {
    autoExecute = true;
    readinessBlockers = ['Broker session is not connected.'];
    render(<TradingModeControls />);
    expect(screen.getByText(/AUTO armed but BLOCKED/)).toBeInTheDocument();
  });
});

describe('live safety status', () => {
  it('shows nothing extra when the status endpoint does not exist', () => {
    render(<TradingModeControls />);
    expect(screen.queryByTestId('live-status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('summarises a healthy state', () => {
    liveResult = available();
    render(<TradingModeControls />);
    const status = screen.getByTestId('live-status');
    expect(within(status).getByText('Safe mode off')).toBeInTheDocument();
    expect(within(status).getByText('Entries enabled')).toBeInTheDocument();
    expect(within(status).getByText('Broker exposure Fresh · 4s')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('flags a stale broker exposure observation', () => {
    liveResult = available({ exposure_observation: { fresh: false, age_s: 180 } });
    render(<TradingModeControls />);
    expect(screen.getByText('Broker exposure Stale · 3m')).toBeInTheDocument();
  });

  it('shows the kill switch as entries disabled', () => {
    liveResult = available({ live_execution: false });
    render(<TradingModeControls />);
    expect(screen.getByText('Entries disabled')).toBeInTheDocument();
  });

  it('degrades when the payload is partial or malformed', () => {
    liveResult = { available: true, status: normalizeLiveStatus({ safe_mode: 'broken', exposure_observation: null }) };
    render(<TradingModeControls />);
    expect(screen.getByText('Safe mode unknown')).toBeInTheDocument();
    expect(screen.getByText('Entries unknown')).toBeInTheDocument();
  });

  it('treats a state other than NORMAL as engaged (fail closed)', () => {
    liveResult = available({ safe_mode: { state: 'LOCKED', reason: null, triggers: [] } });
    render(<TradingModeControls />);
    expect(screen.getByRole('alert')).toHaveTextContent('Safe mode ON');
  });

  it('shows the reason and offers Resume when safe mode is on', () => {
    liveResult = available({
      safe_mode: { state: 'SAFE_MODE', reason: 'Position mismatch', triggers: ['POSITION_MISMATCH'] },
    });
    render(<TradingModeControls />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Safe mode ON');
    expect(alert).toHaveTextContent('Reason: Position mismatch');
    expect(alert).toHaveTextContent('POSITION_MISMATCH');
    expect(screen.getByRole('button', { name: 'Resume trading' })).toBeInTheDocument();
  });

  it('resumes only after a reason is given and confirmed', () => {
    liveResult = available({ safe_mode: { state: 'SAFE_MODE', reason: 'Operator', triggers: ['OPERATOR'] } });
    render(<TradingModeControls />);

    fireEvent.click(screen.getByRole('button', { name: 'Resume trading' }));
    expect(resumeMutate).not.toHaveBeenCalled();
    const confirm = screen.getByRole('button', { name: 'Confirm resume' });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Reason for resuming'), { target: { value: 'Checked positions' } });
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);

    expect(resumeMutate).toHaveBeenCalledTimes(1);
    expect(resumeMutate.mock.calls[0][0]).toEqual({ reason: 'Checked positions', acknowledgeInvestigation: false });
  });

  it('needs the investigation acknowledgement when the trigger is not an operator halt', () => {
    liveResult = available({ safe_mode: { state: 'SAFE_MODE', reason: 'Unprotected position', triggers: ['MISSING_PROTECTION'] } });
    render(<TradingModeControls />);

    fireEvent.click(screen.getByRole('button', { name: 'Resume trading' }));
    fireEvent.change(screen.getByLabelText('Reason for resuming'), { target: { value: 'Stops re-placed' } });
    const confirm = screen.getByRole('button', { name: 'Confirm resume' });
    expect(confirm).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox'));
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);
    expect(resumeMutate.mock.calls[0][0]).toEqual({ reason: 'Stops re-placed', acknowledgeInvestigation: true });
  });

  it('reads the backend live-status shape (object execution, recovery.overall, exposure.age_seconds)', () => {
    liveResult = {
      available: true,
      status: normalizeLiveStatus({
        safe_mode: { state: 'NORMAL', active: false, triggers: [], reason: null },
        live_execution: { enabled: false, kill_switch: true },
        recovery: { overall: 'RECOVERY_REQUIRED' },
        exposure: { observed: true, fresh: false, age_seconds: 95 },
      }),
    };
    render(<TradingModeControls />);
    const status = screen.getByTestId('live-status');
    expect(within(status).getByText('Entries disabled')).toBeInTheDocument();
    expect(within(status).getByText('Recovery: recovery required')).toBeInTheDocument();
    expect(within(status).getByText('Broker exposure Stale · 1m')).toBeInTheDocument();
  });

  it('says so when broker exposure has never been observed', () => {
    liveResult = {
      available: true,
      status: normalizeLiveStatus({
        safe_mode: { state: 'NORMAL', triggers: [] },
        live_execution: { enabled: true, kill_switch: false },
        recovery: { overall: 'CLEAN' },
        exposure: { observed: false, fresh: false, age_seconds: null },
      }),
    };
    render(<TradingModeControls />);
    expect(screen.getByText('Broker exposure Never observed')).toBeInTheDocument();
  });
});

describe('Safeguards', () => {
  it('are always shown, with the production preset', () => {
    render(<TradingModeControls />);
    expect(screen.getByText('SAFEGUARDS')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Load Production Preset/ })).toBeInTheDocument();
  });

  it('Square-Off All needs a confirmation', () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Square-Off All' }));
    expect(squareOffMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Square-Off' }));
    expect(squareOffMutate).toHaveBeenCalledTimes(1);
  });

  it('Kill Switch & Halt needs a confirmation', () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByRole('button', { name: 'Kill Switch & Halt' }));
    expect(haltMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Kill Switch' }));
    expect(haltMutate).toHaveBeenCalledTimes(1);
  });

  it('the production preset needs a confirmation', () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByRole('button', { name: /Load Production Preset/ }));
    expect(presetMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply Preset' }));
    expect(presetMutate).toHaveBeenCalledTimes(1);
  });
});

describe('normalizeLiveStatus', () => {
  it('returns null for non-objects', () => {
    expect(normalizeLiveStatus(null)).toBeNull();
    expect(normalizeLiveStatus('x')).toBeNull();
  });

  it('keeps unreadable fields as unknown rather than inventing defaults', () => {
    const s = normalizeLiveStatus({ live_execution: 'yes', exposure_observation: { fresh: 'y', age_s: 'z' } });
    expect(s?.liveExecution).toBeNull();
    expect(s?.safeMode.state).toBeNull();
    expect(s?.exposure).toEqual({ observed: null, fresh: null, ageS: null });
  });
});

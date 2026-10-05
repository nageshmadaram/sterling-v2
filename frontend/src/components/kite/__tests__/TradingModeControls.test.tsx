/**
 * Rendering a panel must never place an order or change a trading mode.
 *
 * Written after both confirm handlers were left as unbraced arrow functions:
 * the body ended at the first semicolon, so the statement that followed became
 * component-body code and ran on every render. Opening this panel switched the
 * account to LIVE and turned auto-execution ON, with no click, and each success
 * re-rendered and did it again — which is what surfaced as "Maximum update
 * depth exceeded".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

const activateMutate = vi.fn();
const patchMutate = vi.fn();
const refetchReadiness = vi.fn();

let accountIsPaper = true;
let readinessData: any;
const blockedReadiness = () => ({
  ready_for_live: false,
  blockers: ['Live capital entry is disabled by the release-wide code-level execution floor.'],
  warnings: [], checks: {}, is_live_account: true, account_label: 'Live', timestamp_ms: 1,
});

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({
    data: {
      accounts: accountIsPaper
        ? [
            {
              id: 'PAPER-1',
              is_active: true,
              is_paper: true,
              connected: true,
              has_credentials: false,
              label: 'Paper',
            },
            {
              id: 'LIVE-1',
              is_active: false,
              is_paper: false,
              connected: true,
              has_credentials: true,
              label: 'Live',
            },
          ]
        : [
            {
              id: 'PAPER-1',
              is_active: false,
              is_paper: true,
              connected: true,
              has_credentials: false,
              label: 'Paper',
            },
            {
              id: 'LIVE-1',
              is_active: true,
              is_paper: false,
              connected: true,
              has_credentials: true,
              label: 'Live',
            },
          ],
    },
  }),
  useActivateKiteAccount: () => ({ mutate: activateMutate, isPending: false }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { auto_execute: false, vehicle: 'otm_options', target_delta: 0.5 } }),
  usePatchEngineConfig: () => ({ mutate: patchMutate, isPending: false }),
  useEngineReadiness: () => ({
    data: readinessData,
    refetch: refetchReadiness,
  }),
  useApplyProductionConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEmergencySquareOff: () => ({ mutate: vi.fn(), isPending: false }),
  useEmergencyHalt: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

import { TradingModeControls } from '../TradingModeControls';

beforeEach(() => {
  activateMutate.mockReset();
  patchMutate.mockReset();
  accountIsPaper = true;
  readinessData = blockedReadiness();
  refetchReadiness.mockReset();
  refetchReadiness.mockImplementation(async () => ({ data: readinessData }));
});
afterEach(cleanup);

describe('TradingModeControls', () => {
  it('allows live broker observation while capital entry blockers remain active', () => {
    render(<TradingModeControls />);

    fireEvent.click(screen.getByRole('button', { name: 'Select live account' }));

    expect(screen.getByRole('button', { name: /Use LIVE account/i })).not.toBeDisabled();
    expect(
      screen.getByText(/Capital-entry blockers remain active after this mode change/i),
    ).toBeInTheDocument();
  });

  it('blocks automatic entry until a live account is selected', () => {
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    expect(screen.getByRole('button', { name: 'Blockers Exist' })).toBeDisabled();
    expect(screen.getByText('Select a live broker account.')).toBeInTheDocument();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('keeps the release floor authoritative for a live account', () => {
    accountIsPaper = false;
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    expect(screen.getByRole('button', { name: 'Blockers Exist' })).toBeDisabled();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('rechecks readiness before arming automatic entry', async () => {
    accountIsPaper = false;
    readinessData = { ...blockedReadiness(), ready_for_live: true, blockers: [] };
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(patchMutate).toHaveBeenCalledWith({ auto_execute: true }, expect.any(Object)));
    expect(refetchReadiness).toHaveBeenCalledTimes(2);
  });

  it('refuses a readiness failure that occurs while confirmation is open', async () => {
    accountIsPaper = false;
    readinessData = { ...blockedReadiness(), ready_for_live: true, blockers: [] };
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockResolvedValue({ data: blockedReadiness() });
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('renders without changing anything', () => {
    render(<TradingModeControls />);
    // Rendering must never activate another broker account or arm AUTO.
    expect(activateMutate).not.toHaveBeenCalled();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('refuses cached readiness when its refresh reports an error', async () => {
    accountIsPaper = false;
    readinessData = { ...blockedReadiness(), ready_for_live: true, blockers: [] };
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockResolvedValue({ data: readinessData, isError: true });
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('handles a rejected readiness refresh without arming', async () => {
    accountIsPaper = false;
    readinessData = { ...blockedReadiness(), ready_for_live: true, blockers: [] };
    render(<TradingModeControls />);
    fireEvent.click(screen.getByText('AUTO'));
    refetchReadiness.mockRejectedValue(new Error('offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Arm Auto' }));
    await waitFor(() => expect(refetchReadiness).toHaveBeenCalledTimes(2));
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('re-rendering still changes nothing', () => {
    const { rerender } = render(<TradingModeControls />);
    rerender(<TradingModeControls />);
    rerender(<TradingModeControls />);
    expect(activateMutate).not.toHaveBeenCalled();
    expect(patchMutate).not.toHaveBeenCalled();
  });

  it('describes live account mode separately from capital permission', () => {
    accountIsPaper = false;
    render(<TradingModeControls />);

    expect(screen.getByText(/LIVE ACCOUNT — Sterling reads the real Zerodha account/i))
      .toBeInTheDocument();
    expect(screen.queryByText(/real option orders automatically/i)).toBeNull();
  });

  it('going live needs a click and then a confirmation', () => {
    render(<TradingModeControls />);
    expect(activateMutate).not.toHaveBeenCalled();

    // The LIVE side of the toggle only opens the confirmation.
    const live = screen.getByRole('button', { name: 'Select live account' });
    fireEvent.click(live);
    expect(activateMutate).not.toHaveBeenCalled();
  });

  it('offers no simulation selector, including with a legacy account active', () => {
    render(<TradingModeControls />);
    expect(screen.queryByText(/paper|simulation/i)).toBeNull();
    expect(screen.getByRole('button', { name: 'Select live account' })).toBeInTheDocument();
    expect(activateMutate).not.toHaveBeenCalled();
  });

  it('going live activates the existing LIVE account after confirmation', () => {
    render(<TradingModeControls />);

    fireEvent.click(screen.getByRole('button', { name: 'Select live account' }));
    fireEvent.click(screen.getByRole('button', { name: /Use LIVE account/i }));

    expect(activateMutate).toHaveBeenCalledTimes(1);
    expect(activateMutate.mock.calls[0][0]).toBe('LIVE-1');
  });
});

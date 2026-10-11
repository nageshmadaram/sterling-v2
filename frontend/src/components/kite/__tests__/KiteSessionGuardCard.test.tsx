import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

let status: Record<string, unknown> = {};
const openKiteLogin = vi.fn();
const retrySession = vi.fn();
const resetLogin = vi.fn();
let loginPhase = 'idle';

vi.mock('../../../hooks/useKite', () => ({
  useKiteStatus: () => ({ data: status, isFetching: false, refetch: retrySession }),
  useKiteAuthBroadcast: () => {},
  useGenerateKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useOpenKiteLogin: () => ({ open: openKiteLogin, opening: loginPhase === 'waiting', phase: loginPhase, error: null, dismiss: resetLogin }),
  useRefreshKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { KiteSessionGuard } from '../KiteSessionGuard';
import { useKiteStatusCardStore } from '../../../store/useKiteStatusCardStore';

function renderGuard(onOpenAccountSettings = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KiteSessionGuard onOpenAccountSettings={onOpenAccountSettings} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  openKiteLogin.mockClear();
  retrySession.mockClear();
  resetLogin.mockClear();
  loginPhase = 'idle';
  useKiteStatusCardStore.setState({ isOpen: false });
  status = { connected: false, account_id: 'a1', user_name: 'TestUser' };
});

describe('KiteSessionGuard floating card popover', () => {
  it('distinguishes waiting for broker sign-in from opening the login window', () => {
    loginPhase = 'waiting';
    renderGuard();
    expect(screen.getByRole('button', { name: 'Finish login in the Kite window' })).toBeDisabled();
    expect(screen.queryByText('Opening Login…')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Start login again' }));
    expect(resetLogin).toHaveBeenCalledTimes(1);
  });
  it('shows a settled permission error without claiming expiry or spinning forever', () => {
    status = { connected: false, transient: true, account_id: 'a1', message: 'Could not reach Kite to check the session: Insufficient permission for that call.' };
    renderGuard();
    act(() => useKiteStatusCardStore.getState().openCard());
    expect(screen.getByText('Kite access denied')).toBeInTheDocument();
    expect(screen.getByLabelText('Kite session unverified')).toHaveTextContent('UNVERIFIED');
    expect(screen.getByText(/Saved Kite account/)).toBeInTheDocument();
    expect(screen.queryByText('Checking Kite session…')).toBeNull();
    expect(screen.queryByText('No Account Logged In')).toBeNull();
    expect(document.querySelector('.mls-progress-runner')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry session check' }));
    expect(retrySession).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /Open Kite Login/i }));
    expect(openKiteLogin).toHaveBeenCalledTimes(1);
  });

  it('shows a completed network error with a retry action', () => {
    status = { connected: false, transient: true, account_id: 'a1', message: 'Kite request timed out.' };
    renderGuard();
    act(() => useKiteStatusCardStore.getState().openCard());
    expect(screen.getByText('Kite session check unavailable')).toBeInTheDocument();
    expect(screen.getByText('Kite request timed out.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry session check' })).toBeEnabled();
  });


  it('keeps Kite login reachable while the broker session is offline', () => {
    renderGuard();
    const login = screen.getByRole('button', { name: /Open Kite Login/i });
    expect(login).toBeEnabled();
    fireEvent.click(login);
    expect(openKiteLogin).toHaveBeenCalledTimes(1);
  });

  it('auto-opens when Kite is offline on cold load', () => {
    renderGuard();
    expect(screen.getByText('Zerodha Kite Offline')).toBeInTheDocument();
    expect(screen.getByText('Session off')).toBeInTheDocument();
    expect(screen.getByText('Workspace')).toBeInTheDocument();
    expect(screen.getByText('Market off')).toBeInTheDocument();
  });

  it('can be toggled open via useKiteStatusCardStore when connected', () => {
    status = { connected: true, user_name: 'Madaram', account_id: 'a1' };
    renderGuard();
    expect(screen.queryByText('Zerodha Kite Active')).toBeNull();

    act(() => {
      useKiteStatusCardStore.getState().openCard();
    });

    expect(screen.getByText('Zerodha Kite Active')).toBeInTheDocument();
    expect(screen.getByText(/Signed in as Madaram/i)).toBeInTheDocument();
  });

  it('invokes onOpenAccountSettings and closes card when Account Settings link is clicked', () => {
    const onOpenAccountSettings = vi.fn();
    renderGuard(onOpenAccountSettings);

    const link = screen.getByText(/Account & API Settings/i);
    fireEvent.click(link);

    expect(onOpenAccountSettings).toHaveBeenCalledTimes(1);
    expect(useKiteStatusCardStore.getState().isOpen).toBe(false);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginGate } from '../LoginGate';
import { notifyAuthExpired } from '../../../utils/liveSync';
import { getAccessToken, setAuthTokens } from '../../../utils/auth';

vi.mock('../../../utils/api', () => ({
  api: { get: vi.fn(async () => ({ id: 'u1' })), post: vi.fn() },
}));

function WorkspaceProbe({ onMount }: { onMount: () => void }) {
  useEffect(() => { onMount(); }, [onMount]);
  return <div data-testid="dashboard">dashboard</div>;
}

function renderGate(qc = new QueryClient()) {
  render(
    <QueryClientProvider client={qc}>
      <LoginGate><div data-testid="dashboard">dashboard</div></LoginGate>
    </QueryClientProvider>,
  );
  return qc;
}

beforeEach(() => {
  sessionStorage.clear();
  setAuthTokens({ access_token: 'a', refresh_token: 'r' });
});
afterEach(() => sessionStorage.clear());

describe('LoginGate session expiry', () => {
  it('retains a valid session across a temporary backend restart', async () => {
    const { api } = await import('../../../utils/api');
    vi.mocked(api.get).mockRejectedValueOnce(new Error('reconnecting')).mockResolvedValueOnce({ id:'u1' });
    renderGate();
    await screen.findByText('Sterling is reconnecting. Your saved settings and session are retained.');
    expect(screen.queryByRole('form',{name:'Sterling login'})).toBeNull();
    expect(getAccessToken()).toBe('a');
    fireEvent.click(screen.getByRole('button',{name:'Retry connection'}));
    await waitFor(()=>expect(screen.getByTestId('dashboard').parentElement).not.toHaveAttribute('inert'));
    expect(getAccessToken()).toBe('a');
  });

  it('does not mount workspace effects or subscriptions while session validation is pending', async () => {
    const { api } = await import('../../../utils/api');
    let complete!: (value: unknown) => void;
    vi.mocked(api.get).mockReturnValueOnce(new Promise(resolve => {complete = resolve;}));
    const mounted = vi.fn();
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <LoginGate><WorkspaceProbe onMount={mounted} /></LoginGate>
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId('dashboard')).not.toBeInTheDocument();
    expect(mounted).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Verifying Sterling session');
    await act(async () => complete({id:'u1'}));
    await waitFor(() => expect(screen.getByTestId('dashboard')).toBeInTheDocument());
    expect(mounted).toHaveBeenCalledTimes(1);
  });
  it('leaves the dashboard, drops cached data and says why when the session expires', async () => {
    const qc = renderGate();
    qc.setQueryData(['kite-positions'], { net: [{ tradingsymbol: 'NIFTY' }] });
    await waitFor(() => expect(screen.queryByText('Verifying Sterling session…')).toBeNull());

    act(() => notifyAuthExpired());

    await waitFor(() => expect(screen.queryByTestId('dashboard')).toBeNull());
    expect(screen.getByRole('alert').textContent).toMatch(/session expired/i);
    expect(qc.getQueryData(['kite-positions'])).toBeUndefined();
    expect(getAccessToken()).toBe('');
  });

  it('ignores an expiry signal while the login form is already showing', async () => {
    const { api } = await import('../../../utils/api');
    (api.get as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error('no'), { status: 401 }),
    );
    renderGate();
    await screen.findByRole('form', { name: /sterling login/i });
    act(() => notifyAuthExpired());
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

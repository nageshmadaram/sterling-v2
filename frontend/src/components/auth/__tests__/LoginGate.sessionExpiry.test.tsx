import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginGate } from '../LoginGate';
import { notifyAuthExpired } from '../../../utils/liveSync';
import { getAccessToken, setAuthTokens } from '../../../utils/auth';

vi.mock('../../../utils/api', () => ({
  api: { get: vi.fn(async () => ({ id: 'u1' })), post: vi.fn() },
}));

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
  it('leaves the dashboard, drops cached data and says why when the session expires', async () => {
    const qc = renderGate();
    qc.setQueryData(['kite-positions'], { net: [{ tradingsymbol: 'NIFTY' }] });
    await screen.findByTestId('dashboard');

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

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LoginGate } from '../LoginGate';
import { clearAuthTokens, getAccessToken, getRefreshToken, setAuthTokens } from '../../../utils/auth';

const json = (status: number, value: unknown = {}) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});

function openApp() {
  render(<QueryClientProvider client={new QueryClient()}>
    <LoginGate><div data-testid="dashboard">Mobile dashboard</div></LoginGate>
  </QueryClientProvider>);
}

beforeEach(() => {
  clearAuthTokens();
  localStorage.setItem('kite-settings', '{"saved":"retained"}');
  setAuthTokens({ access_token: 'expired-access', refresh_token: 'remembered-refresh' });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); clearAuthTokens(); localStorage.clear(); });

describe('mobile startup with a remembered session', () => {
  it('renews expired access and opens the full app without asking for login again', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
      const url = String(input);
      if (url.endsWith('/auth/refresh') || url.endsWith('/auth/remember')) {
        return json(200, { access_token: 'renewed-access', refresh_token: 'renewed-refresh' });
      }
      if (url.endsWith('/auth/me')) {
        return new Headers(options?.headers).get('Authorization') === 'Bearer renewed-access'
          ? json(200, { user_id: 'operator' }) : json(401, { detail: 'Access expired' });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    openApp();
    await waitFor(() => expect(screen.getByTestId('dashboard')).toBeInTheDocument());
    expect(screen.queryByRole('form', { name: 'Sterling login' })).toBeNull();
    expect(getAccessToken()).toBe('renewed-access');
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh'))).toHaveLength(1);
    expect(localStorage.getItem('kite-settings')).toBe('{"saved":"retained"}');
  });

  it('shows sign-in when the remembered refresh token is rejected instead of reconnecting forever', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(401, { detail: 'Session revoked' }));
    openApp();
    await screen.findByRole('form', { name: 'Sterling login' });
    expect(screen.queryByText(/Sterling is reconnecting/)).toBeNull();
    expect(getRefreshToken()).toBe('');
    expect(localStorage.getItem('kite-settings')).toBe('{"saved":"retained"}');
  });

  it('keeps the saved session during an outage and recovers when the connection returns', async () => {
    let online = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, options) => {
      const url = String(input);
      if (url.endsWith('/auth/refresh')) {
        return online ? json(200, { access_token: 'renewed-access', refresh_token: 'renewed-refresh' }) : json(503);
      }
      if (url.endsWith('/auth/remember')) return json(503);
      return new Headers(options?.headers).get('Authorization') === 'Bearer renewed-access'
        ? json(200, { user_id: 'operator' }) : json(401);
    });
    openApp();
    await screen.findByText('Sterling is reconnecting. Your saved settings and session are retained.');
    expect(getRefreshToken()).toBe('remembered-refresh');
    online = true;
    fireEvent.click(screen.getByRole('button', { name: 'Retry connection' }));
    await waitFor(() => expect(screen.getByTestId('dashboard')).toBeInTheDocument());
    expect(localStorage.getItem('kite-settings')).toBe('{"saved":"retained"}');
  });

  it('shows sign-in if the replacement access token is also rejected', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => String(input).endsWith('/auth/refresh')
      ? json(200, { access_token: 'rejected-access', refresh_token: 'replacement-refresh' })
      : json(401));
    openApp();
    await screen.findByRole('form', { name: 'Sterling login' });
    expect(screen.queryByText(/Sterling is reconnecting/)).toBeNull();
    expect(getRefreshToken()).toBe('');
  });

  it('removes the reconnect message when a later retry proves the session expired', async () => {
    let revoked = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => String(input).endsWith('/auth/refresh')
      ? json(revoked ? 401 : 503) : json(401));
    openApp();
    await screen.findByText('Sterling is reconnecting. Your saved settings and session are retained.');
    revoked = true;
    fireEvent.click(screen.getByRole('button', { name: 'Retry connection' }));
    await screen.findByRole('form', { name: 'Sterling login' });
    expect(screen.queryByText(/Sterling is reconnecting/)).toBeNull();
    expect(localStorage.getItem('kite-settings')).toBe('{"saved":"retained"}');
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { AUTH_EXPIRED_EVENT } from '../liveSync';

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('API auth scope separation', () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem('sterling.auth.access', 'operator-access');
    sessionStorage.setItem('sterling.auth.refresh', 'operator-refresh');
    vi.restoreAllMocks();
  });

  it('does not expire Sterling when only the Kite broker session is rejected', async () => {
    const expired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);

    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/api/v1/kite/orders')) {
          return response(401, { detail: 'Incorrect api_key or access_token.' });
        }
        if (url.endsWith('/api/v1/auth/me')) {
          return response(200, {
            user_id: 'default',
            username: 'admin',
            role: 'admin',
          });
        }
        throw new Error(`unexpected request: ${url}`);
      });

    await expect(api.get('/api/v1/kite/orders')).rejects.toMatchObject({
      status: 401,
      message: 'Incorrect api_key or access_token.',
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(expired).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('sterling.auth.access')).toBe('operator-access');
    expect(sessionStorage.getItem('sterling.auth.refresh')).toBe('operator-refresh');

    window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  });

  it('still expires Sterling when the operator identity is rejected too', async () => {
    const expired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);

    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/api/v1/kite/orders')) {
          return response(401, { detail: 'Authentication required' });
        }
        if (url.endsWith('/api/v1/auth/me')) {
          return response(401, { detail: 'Invalid or expired access token' });
        }
        if (url.endsWith('/api/v1/auth/refresh')) {
          return response(401, { detail: 'Invalid refresh token' });
        }
        throw new Error(`unexpected request: ${url}`);
      });

    await expect(api.get('/api/v1/kite/orders')).rejects.toMatchObject({
      status: 401,
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(expired).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('sterling.auth.access')).toBeNull();
    expect(sessionStorage.getItem('sterling.auth.refresh')).toBeNull();

    window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  });

  it('does not force logout when operator verification is temporarily unreachable', async () => {
    const expired = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);

    vi.spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith('/api/v1/kite/orders')) {
          return response(401, { detail: 'Kite session expired' });
        }
        if (url.endsWith('/api/v1/auth/me')) {
          throw new TypeError('network unavailable');
        }
        if (url.endsWith('/api/v1/auth/refresh')) {
          throw new TypeError('network unavailable');
        }
        throw new Error(`unexpected request: ${url}`);
      });

    await expect(api.get('/api/v1/kite/orders')).rejects.toMatchObject({
      status: 401,
    });

    expect(expired).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('sterling.auth.access')).toBe('operator-access');
    expect(sessionStorage.getItem('sterling.auth.refresh')).toBe('operator-refresh');

    window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  });
});

it('never scopes administration or access requests to a simulation book',async()=>{
  localStorage.setItem('sterling.view','simulation');
  const calls: RequestInit[]=[];
  vi.spyOn(globalThis,'fetch').mockImplementation(async(_input,init)=>{calls.push(init || {});return new Response('{}',{status:200,headers:{'Content-Type':'application/json'}});});
  try {
    await api.get('/api/v1/admin/access-requests');
    await api.post('/api/v1/settings/access-requests',{feature:'radio'});
    expect(calls.every(call=>!new Headers(call.headers).has('X-Sterling-View'))).toBe(true);
  } finally {localStorage.removeItem('sterling.view');vi.restoreAllMocks();}
});

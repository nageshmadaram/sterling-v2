import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { setAuthTokens } from '../auth';
import { AUTH_EXPIRED_EVENT, __resetLiveSync, onResync } from '../liveSync';

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

let resyncs: string[];
let expired: number;
const onExpired = () => { expired += 1; };

beforeEach(() => {
  resyncs = [];
  expired = 0;
  sessionStorage.clear();
  onResync((r) => resyncs.push(r));
  window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
});

afterEach(() => {
  window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  __resetLiveSync();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('writes tell the app to resync', () => {
  it('after a successful order placement', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { order_id: '1' })));
    await api.post('/api/v1/kite/orders', { tradingsymbol: 'X' });
    expect(resyncs).toEqual(['write']);
  });

  it('after a rejected order, because a rejection is still an answer to re-read', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(400, { detail: 'Markets are closed' })));
    await expect(api.post('/api/v1/kite/orders', {})).rejects.toThrow('Markets are closed');
    expect(resyncs).toEqual(['write']);
  });

  it('after a network failure: the order may have reached the broker', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down'); }));
    await expect(api.post('/api/v1/kite/orders', {})).rejects.toThrow('network down');
    expect(resyncs).toEqual(['write']);
  });

  it('for an exit and a kill switch', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200)));
    await api.delete('/api/v1/kite/engine/open-positions/NIFTY');
    await api.post('/api/v1/kite/engine/emergency-halt', {});
    expect(resyncs).toEqual(['write', 'write']);
  });

  it('not for reads or margin/charge quotes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, [])));
    await api.get('/api/v1/kite/positions');
    await api.post('/api/v1/kite/margins/orders', []);
    await api.post('/api/v1/kite/ticker/subscribe', { instrument_tokens: [1] });
    expect(resyncs).toEqual([]);
  });
});

describe('session expiry', () => {
  it('signals expiry when the refresh token is rejected', async () => {
    setAuthTokens({ access_token: 'old', refresh_token: 'revoked' });
    vi.stubGlobal('fetch', vi.fn(async (url: string) =>
      String(url).includes('/auth/refresh') ? json(401) : json(401)));
    await expect(api.get('/api/v1/kite/positions')).rejects.toMatchObject({ status: 401 });
    expect(expired).toBe(1);
  });

  it('does not signal expiry when the refresh succeeds and the retry works', async () => {
    setAuthTokens({ access_token: 'old', refresh_token: 'good' });
    let calls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/auth/refresh')) return json(200, { access_token: 'new', refresh_token: 'good2' });
      calls += 1;
      return calls === 1 ? json(401) : json(200, { net: [] });
    }));
    await expect(api.get('/api/v1/kite/positions')).resolves.toEqual({ net: [] });
    expect(expired).toBe(0);
  });

  it('does not log the operator out because the refresh call hit a network blip', async () => {
    setAuthTokens({ access_token: 'old', refresh_token: 'good' });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/auth/refresh')) throw new TypeError('offline');
      return json(401);
    }));
    await expect(api.get('/api/v1/kite/positions')).rejects.toMatchObject({ status: 401 });
    expect(expired).toBe(0);
    expect(sessionStorage.getItem('sterling.auth.refresh')).toBe('good');
  });

  it('never signals expiry for the auth endpoints themselves', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(401)));
    await expect(api.get('/api/v1/auth/me')).rejects.toMatchObject({ status: 401 });
    expect(expired).toBe(0);
  });
});

import { beforeEach, afterEach, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ apiHost: 'https://sterling.test', authToken: 'old', refreshToken: 'refresh',
  setSession(access: string, refresh: string) { this.authToken = access; this.refreshToken = refresh; } }));
vi.mock('../src/store/useStore', () => ({ useStore: { getState: () => state } }));
import { api } from '../src/services/api';
let network: ReturnType<typeof vi.fn>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  state.apiHost = 'https://sterling.test'; state.authToken = 'old'; state.refreshToken = 'refresh';
  network = vi.fn(); vi.stubGlobal('fetch', network);
});
afterEach(() => vi.unstubAllGlobals());

it('requires sign-in before calling private APIs', async () => {
  state.authToken = '';
  await expect(api.get('/positions')).rejects.toThrow('Sign in');
  expect(network).not.toHaveBeenCalled();
});
it('renews an expired session and retries only the rejected read', async () => {
  network.mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({ access_token: 'new', refresh_token: 'new-refresh' })).mockResolvedValueOnce(json({ net: [] }));
  await expect(api.get('/positions')).resolves.toEqual({ net: [] });
  expect(network).toHaveBeenCalledTimes(3);
  expect(network.mock.calls[2][1].headers.get('Authorization')).toBe('Bearer new');
});
it('does not automatically replay an order after renewing authentication', async () => {
  network.mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({ access_token: 'new', refresh_token: 'new-refresh' }));
  await expect(api.post('/orders', { quantity: 1 })).rejects.toThrow('Review the order status');
  expect(network).toHaveBeenCalledTimes(2);
});
it('does not replay an ambiguously submitted order', async () => {
  network.mockRejectedValueOnce(new TypeError('connection lost'));
  await expect(api.post('/orders', { quantity: 1 })).rejects.toThrow('could not be confirmed');
  expect(network).toHaveBeenCalledTimes(1);
});
it('retains credentials after a temporary refresh outage', async () => {
  network.mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({}, 503));
  await expect(api.get('/positions')).rejects.toThrow('temporarily unavailable');
  expect(state.authToken).toBe('old');
});
it('drops credentials when refresh credentials are revoked', async () => {
  network.mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({}, 401));
  await expect(api.get('/positions')).rejects.toThrow('expired');
  expect(state.authToken).toBe(''); expect(state.refreshToken).toBe('');
});
it('does not put a previous server session into a newly selected server', async () => {
  network.mockResolvedValueOnce(json({}, 401)).mockImplementationOnce(async () => {
    state.apiHost = 'https://another.test'; state.authToken = 'other'; state.refreshToken = 'other-refresh';
    return json({ access_token: 'wrong-server', refresh_token: 'wrong-refresh' });
  });
  await expect(api.get('/positions')).rejects.toThrow();
  expect(state.authToken).toBe('other');
});

it('treats an unreadable successful order response as an unconfirmed submission', async () => {
  network.mockResolvedValueOnce(new Response('upstream closed', { status: 200 }));
  await expect(api.post('/orders', { quantity: 1 })).rejects.toThrow('could not be confirmed');
  expect(network).toHaveBeenCalledTimes(1);
});

it('shares one refresh across simultaneous expired reads', async () => {
  let complete: (value: Response) => void = () => {};
  const pending = new Promise<Response>(resolve => { complete = resolve; });
  network.mockImplementation(async (url: string, options: RequestInit) => {
    if (url.endsWith('/auth/refresh')) return pending;
    return (options.headers as Headers).get('Authorization') === 'Bearer old'
      ? json({}, 401) : json({ net: [] });
  });
  const first = api.get('/positions'); const second = api.get('/orders');
  await vi.waitFor(() => expect(network.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh'))).toHaveLength(1));
  complete(json({ access_token: 'new', refresh_token: 'new-refresh' }));
  await expect(Promise.all([first, second])).resolves.toEqual([{ net: [] }, { net: [] }]);
});

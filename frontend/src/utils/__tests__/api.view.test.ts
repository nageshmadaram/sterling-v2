import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import { setAuthTokens } from '../auth';
import { useViewStore } from '../../store/useViewStore';

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const headerOf = (call: unknown[]): string | null =>
  new Headers((call[1] as RequestInit | undefined)?.headers).get('X-Sterling-View');

beforeEach(() => {
  sessionStorage.clear();
  useViewStore.setState({ view: 'live' });
});
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  useViewStore.setState({ view: 'live' });
});

describe('the view header', () => {
  it('is absent in the live view, so live traffic is exactly what it always was', async () => {
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await api.get('/api/v1/kite/positions');
    await api.post('/api/v1/kite/orders', {});
    expect(fetchMock.mock.calls.map(headerOf)).toEqual([null, null]);
  });

  it('is X-Sterling-View: simulation on every verb in the Simulation view', async () => {
    useViewStore.setState({ view: 'simulation' });
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await api.get('/api/v1/kite/positions');
    await api.post('/api/v1/kite/orders', { a: 1 });
    await api.put('/api/v1/kite/engine/config', { a: 1 });
    await api.patch('/api/v1/kite/engine/config', { a: 1 });
    await api.delete('/api/v1/kite/orders/1');
    expect(fetchMock.mock.calls).toHaveLength(5);
    expect(fetchMock.mock.calls.map(headerOf)).toEqual(Array(5).fill('simulation'));
  });

  it('keeps the bearer token and a JSON content type next to it', async () => {
    setAuthTokens({ access_token: 'tok', refresh_token: 'ref' });
    useViewStore.setState({ view: 'simulation' });
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await api.put('/api/v1/sim/capital', { capital: 5 });
    const sent = new Headers(((fetchMock.mock.calls[0] as unknown[])[1] as RequestInit).headers);
    expect(sent.get('Authorization')).toBe('Bearer tok');
    expect(sent.get('Content-Type')).toBe('application/json');
    expect(sent.get('X-Sterling-View')).toBe('simulation');
  });

  it('follows a switch made between two requests', async () => {
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await api.get('/api/v1/kite/positions');
    useViewStore.setState({ view: 'simulation' });
    await api.get('/api/v1/kite/positions');
    useViewStore.setState({ view: 'live' });
    await api.get('/api/v1/kite/positions');
    expect(fetchMock.mock.calls.map(headerOf)).toEqual([null, 'simulation', null]);
  });

  it('is repeated on the retry after a token refresh, in the same view as the first try', async () => {
    setAuthTokens({ access_token: 'old', refresh_token: 'good' });
    useViewStore.setState({ view: 'simulation' });
    let positionCalls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes('/auth/refresh')) return json(200, { access_token: 'new', refresh_token: 'good' });
      positionCalls += 1;
      if (positionCalls === 1) {
        // The view flips while the refresh is in flight; the retry must stay in the view it began in.
        useViewStore.setState({ view: 'live' });
        return json(401);
      }
      return json(200, { ok: true });
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.get('/api/v1/journal/summary')).resolves.toEqual({ ok: true });
    const positionHeaders = fetchMock.mock.calls
      .filter((c) => String((c as unknown[])[0]).includes('/journal/summary'))
      .map(headerOf);
    expect(positionHeaders).toEqual(['simulation', 'simulation']);
  });
});

/**
 * Behaviour of the single per-tab stream socket: reconnect, resync triggers,
 * staleness, order channel, cross-tab subscription safety.
 *
 * The socket is a hand-driven double and time is fake, so every assertion is
 * about what the module does in response to a specific wire event.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readyState = 0;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((event?: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  send(data: string) { this.sent.push(JSON.parse(data)); }

  close() {
    this.closeCalls += 1;
    this.readyState = 3;
    // Real sockets deliver the close event asynchronously.
    queueMicrotask(() => this.onclose?.());
  }

  // ── test drivers ──
  open() { this.readyState = 1; this.onopen?.(); }
  recv(frame: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(frame) }); }
  drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
  channels() {
    return this.sent.filter((m) => m.action === 'subscribe').map((m) => m.channel as string);
  }
}

type Mod = typeof import('../useKiteLiveTicks');
let mod: Mod;
let post: ReturnType<typeof vi.fn>;
let bus: typeof import('../../utils/liveSync');
let store: typeof import('../../store/useStreamHealthStore');
let resyncs: string[];

const live = () => MockWebSocket.instances[MockWebSocket.instances.length - 1];

async function connectAndAuth(userId = 'u1') {
  const ws = live();
  ws.open();
  ws.recv({ type: 'auth_ok', user_id: userId });
  return ws;
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-05T09:30:00Z'));
  vi.resetModules();
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  sessionStorage.setItem('sterling.auth.access', 'jwt-for-test');
  vi.doMock('../../utils/api', () => ({
    api: { post: vi.fn(async () => ({ ok: true })), get: vi.fn() },
  }));
  const api = await import('../../utils/api');
  post = api.api.post as unknown as ReturnType<typeof vi.fn>;
  bus = await import('../../utils/liveSync');
  store = await import('../../store/useStreamHealthStore');
  mod = await import('../useKiteLiveTicks');
  resyncs = [];
  bus.onResync((r) => resyncs.push(r));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.doUnmock('../../utils/api');
  sessionStorage.clear();
});

describe('one authenticated socket per tab', () => {
  it('opens a single socket for ticks and orders and authenticates in the first frame', async () => {
    const offTicks = mod.registerTokens([111]);
    const offOrders = mod.subscribeKiteOrderUpdates(() => {});
    const offMore = mod.registerTokens([222]);

    expect(MockWebSocket.instances).toHaveLength(1);
    const ws = live();
    expect(ws.url).not.toContain('jwt-for-test');
    ws.open();
    expect(ws.sent[0]).toEqual({ action: 'auth', token: 'jwt-for-test' });
    expect(ws.channels()).toEqual([]);          // nothing subscribed before auth_ok

    ws.recv({ type: 'auth_ok', user_id: 'u1' });
    expect(ws.channels()).toEqual(['kite_ticks:u1', 'kite_orders:u1']);

    offTicks(); offOrders(); offMore();
  });

  it('closes the socket and stops reconnecting once the last consumer leaves', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    off();
    expect(ws.closeCalls).toBe(1);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(store.useStreamHealthStore.getState().transport).toBe('idle');
  });

  it('does not open a second socket while one is connecting or open', async () => {
    const a = mod.registerTokens([1]);
    const b = mod.registerTokens([2]);
    live().open();
    const c = mod.registerTokens([3]);
    expect(MockWebSocket.instances).toHaveLength(1);
    a(); b(); c();
  });
});

describe('reconnect', () => {
  it('refreshes expired authentication before reconnecting with the new token', async () => {
    sessionStorage.setItem('sterling.auth.refresh', 'refresh-for-test');
    const refresh = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({ ok: true, json: async () => ({ access_token: 'new-access', refresh_token: 'new-refresh' }) }));
    vi.stubGlobal('fetch', refresh);
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.drop(4401);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(String(refresh.mock.calls[0][0])).toContain('/api/v1/auth/refresh');
    expect(MockWebSocket.instances).toHaveLength(2);
    live().open();
    expect(live().sent[0]).toEqual({ action: 'auth', token: 'new-access' });
    off();
  });

  it('stops reconnecting when a revoked identity cannot refresh', async () => {
    sessionStorage.setItem('sterling.auth.refresh', 'revoked-refresh');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })));
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.drop(4401);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(sessionStorage.getItem('sterling.auth.access')).toBeNull();
    expect(sessionStorage.getItem('sterling.auth.refresh')).toBeNull();
    off();
  });

  it('backs off exponentially with jitter, capped', () => {
    const lo = () => 0;     // minimum jitter
    const hi = () => 0.9999;
    expect(mod.backoffDelay(0, lo)).toBeGreaterThanOrEqual(700);
    expect(mod.backoffDelay(0, hi)).toBeLessThanOrEqual(1300);
    expect(mod.backoffDelay(1, lo)).toBeGreaterThan(mod.backoffDelay(0, lo));
    expect(mod.backoffDelay(2, lo)).toBeGreaterThan(mod.backoffDelay(1, lo));
    expect(mod.backoffDelay(20, hi)).toBeLessThanOrEqual(13_000);
    expect(mod.backoffDelay(20, lo)).toBeGreaterThanOrEqual(7_000);
    // Two tabs must not share a schedule.
    expect(mod.backoffDelay(3, () => 0.1)).not.toBe(mod.backoffDelay(3, () => 0.9));
  });

  it('reconnects after a drop, and a later drop waits longer than the first', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);   // jitter factor 1.0
    const off = mod.registerTokens([111]);
    live().open();                                    // no auth_ok: never "healthy"
    live().drop();
    expect(MockWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(900);
    expect(MockWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(MockWebSocket.instances).toHaveLength(2);  // after ~1s

    live().open();
    live().drop();                                    // second consecutive failure
    await vi.advanceTimersByTimeAsync(1_500);
    expect(MockWebSocket.instances).toHaveLength(2);  // waits ~2s now
    await vi.advanceTimersByTimeAsync(700);
    expect(MockWebSocket.instances).toHaveLength(3);
    off();
  });

  it('resets the backoff once the server has authenticated us', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const off = mod.registerTokens([111]);
    live().open(); live().drop();
    await vi.advanceTimersByTimeAsync(1_100);
    live().open(); live().drop();
    await vi.advanceTimersByTimeAsync(2_100);
    expect(MockWebSocket.instances).toHaveLength(3);

    await connectAndAuth();           // healthy again
    live().drop();
    await vi.advanceTimersByTimeAsync(1_100);
    expect(MockWebSocket.instances).toHaveLength(4);   // back to ~1s, not 4s
    off();
  });

  it('asks for a resync when the socket comes back, but not on the first connect', async () => {
    const off = mod.registerTokens([111]);
    await connectAndAuth();
    expect(resyncs).toEqual([]);

    live().drop();
    await vi.advanceTimersByTimeAsync(1_500);
    await connectAndAuth();
    expect(resyncs).toEqual(['ws-reconnect']);
    off();
  });

  it('re-subscribes the full token set after a reconnect', async () => {
    const off = mod.registerTokens([111, 222], 'quote');
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);
    post.mockClear();

    live().drop();
    await vi.advanceTimersByTimeAsync(1_500);
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);

    expect(post).toHaveBeenCalledWith(
      '/api/v1/kite/ticker/subscribe', { instrument_tokens: [111, 222], mode: 'quote' },
    );
    off();
  });

  it('reconnects immediately when the browser comes back online', async () => {
    const off = mod.registerTokens([111]);
    await connectAndAuth();
    live().drop();
    expect(MockWebSocket.instances).toHaveLength(1);

    window.dispatchEvent(new Event('online'));
    expect(MockWebSocket.instances).toHaveLength(2);
    off();
  });

  it('replaces a half-open socket when the tab becomes visible again', async () => {
    const off = mod.registerTokens([111]);
    const first = await connectAndAuth();
    await vi.advanceTimersByTimeAsync(20_000);        // laptop slept: nothing arrived

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    expect(first.closeCalls).toBe(1);
    expect(MockWebSocket.instances).toHaveLength(2);
    off();
  });
});

describe('staleness', () => {
  it('replaces a socket that is open but has gone completely silent', async () => {
    const off = mod.registerTokens([111]);
    const first = await connectAndAuth();
    await vi.advanceTimersByTimeAsync(mod.WATCHDOG_STALE_MS + 3_000);

    expect(first.closeCalls).toBe(1);
    expect(MockWebSocket.instances.length).toBeGreaterThanOrEqual(2);
    off();
  });

  it('heartbeats keep an idle socket alive', async () => {
    const off = mod.registerTokens([111]);
    const first = await connectAndAuth();
    for (let i = 0; i < 8; i++) {
      await vi.advanceTimersByTimeAsync(10_000);
      first.recv({ type: 'heartbeat', ts_ms: Date.now(), boot_id: 'boot-a' });
    }
    expect(first.closeCalls).toBe(0);
    expect(MockWebSocket.instances).toHaveLength(1);
    off();
  });

  it('stops trusting ticks once the feed goes quiet, so REST can take over', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 101.5 }] });
    expect(mod.getTick(111)?.last_price).toBe(101.5);

    ws.recv({ type: 'heartbeat', boot_id: 'b' });     // heartbeat is not a tick
    await vi.advanceTimersByTimeAsync(mod.TICK_TRUST_MS + 1_000);
    ws.recv({ type: 'heartbeat', boot_id: 'b' });
    expect(mod.getTick(111), 'a frozen tick must not beat fresher REST data').toBeUndefined();

    ws.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 102 }] });
    expect(mod.getTick(111)?.last_price).toBe(102);
    off();
  });

  it('forgets ticks when the socket drops', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 99 }] });
    ws.drop();
    expect(mod.getTick(111)).toBeUndefined();
    expect(mod.tickFeedAgeMs()).toBeNull();
    off();
  });

  it('reports transport state to the health store', async () => {
    const off = mod.registerTokens([111]);
    const s = () => store.useStreamHealthStore.getState();
    expect(s().transport).toBe('connecting');
    const ws = await connectAndAuth();
    expect(s().transport).toBe('open');
    ws.drop();
    expect(s().transport).toBe('closed');
    off();
    expect(s().transport).toBe('idle');
  });
});

describe('backend restart', () => {
  it('resyncs and re-subscribes when the heartbeat boot_id changes', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'heartbeat', boot_id: 'boot-a', ts_ms: 1 });
    await vi.advanceTimersByTimeAsync(100);
    post.mockClear();

    ws.recv({ type: 'heartbeat', boot_id: 'boot-a', ts_ms: 2 });
    expect(resyncs).toEqual([]);

    ws.recv({ type: 'heartbeat', boot_id: 'boot-b', ts_ms: 3 });
    expect(resyncs).toEqual(['boot-change']);
    await vi.advanceTimersByTimeAsync(100);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/kite/ticker/subscribe', expect.objectContaining({ instrument_tokens: [111] }),
    );
    expect(store.useStreamHealthStore.getState().bootId).toBe('boot-b');
    off();
  });
});

describe('broker feed status', () => {
  it('records the broker status and distrusts held ticks while the broker feed is down', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 50 }] });
    expect(mod.getTick(111)).toBeDefined();

    ws.recv({
      type: 'kite_stream_status', started: true, connected: false,
      last_tick_ms: 1234, last_error: 'ConnectionClosedError',
    });
    expect(store.useStreamHealthStore.getState().broker).toMatchObject({
      started: true, connected: false, lastError: 'ConnectionClosedError',
    });
    expect(mod.getTick(111)).toBeUndefined();
    off();
  });

  it('resyncs when the broker feed returns, because order updates were lost meanwhile', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_stream_status', started: true, connected: true });
    ws.recv({ type: 'kite_stream_status', started: true, connected: false });
    expect(resyncs).toEqual([]);
    ws.recv({ type: 'kite_stream_status', started: true, connected: true });
    expect(resyncs).toEqual(['broker-reconnect']);
    off();
  });

  it('asks the backend to rebuild a ticker that was torn down, at most every few seconds', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);
    post.mockClear();

    ws.recv({ type: 'kite_stream_status', started: false, connected: false });
    await vi.advanceTimersByTimeAsync(100);
    expect(post).toHaveBeenCalledTimes(1);

    post.mockClear();
    ws.recv({ type: 'kite_stream_status', started: false, connected: false });
    await vi.advanceTimersByTimeAsync(100);
    expect(post, 'a status loop must not turn into a subscribe loop').not.toHaveBeenCalled();
    off();
  });
});

describe('order channel', () => {
  it('delivers order updates to every listener and isolates a throwing one', async () => {
    const seen: unknown[] = [];
    const offBad = mod.subscribeKiteOrderUpdates(() => { throw new Error('bad listener'); });
    const offGood = mod.subscribeKiteOrderUpdates((o) => seen.push(o));
    const ws = await connectAndAuth();

    ws.recv({ type: 'kite_order_update', order: { order_id: 'A1', status: 'COMPLETE' } });
    expect(seen).toEqual([{ order_id: 'A1', status: 'COMPLETE' }]);

    offGood();
    ws.recv({ type: 'kite_order_update', order: { order_id: 'A2' } });
    expect(seen).toHaveLength(1);
    offBad();
  });

  it('ignores malformed order frames', async () => {
    const seen: unknown[] = [];
    const off = mod.subscribeKiteOrderUpdates((o) => seen.push(o));
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_order_update' });
    ws.recv({ type: 'kite_order_update', order: 'nope' });
    expect(seen).toEqual([]);
    off();
  });
});

describe('symbol-selective React notification revisions', () => {
  it('does not invalidate an unrelated quote consumer when another token ticks', async () => {
    const off = mod.registerTokens([111, 222]);
    const ws = await connectAndAuth();
    const a0 = mod.tokenDisplayRevision('111');
    const b0 = mod.tokenDisplayRevision('222');
    ws.recv({ type:'kite_ticks', ticks:[{ instrument_token:111, last_price:100 }] });
    await vi.advanceTimersByTimeAsync(80);
    expect(mod.tokenDisplayRevision('111')).not.toBe(a0);
    expect(mod.tokenDisplayRevision('222')).toBe(b0);
    const a1 = mod.tokenDisplayRevision('111');
    ws.recv({ type:'kite_ticks', ticks:[{ instrument_token:222, last_price:201 }] });
    await vi.advanceTimersByTimeAsync(80);
    expect(mod.tokenDisplayRevision('111')).toBe(a1);
    expect(mod.tokenDisplayRevision('222')).not.toBe(b0);
    off();
  });
  it('invalidates all price consumers when a session resets', async () => {
    const off = mod.registerTokens([111]);
    await connectAndAuth();
    const previous = mod.tokenDisplayRevision('111,222');
    mod.resetLiveTickState();
    expect(mod.tokenDisplayRevision('111,222')).not.toBe(previous);
    off();
  });
});

describe('subscription reconcile', () => {
  it('retries a failed subscribe instead of leaving the board without ticks', async () => {
    post.mockRejectedValueOnce(new Error('503'));
    const off = mod.registerTokens([111]);
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);
    expect(post).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(2_500);
    expect(post).toHaveBeenCalledTimes(2);
    off();
  });

  it('does not unsubscribe a token another tab still shows', async () => {
    const offB = mod.registerTokens([999]);     // keeps the socket alive throughout
    const offA = mod.registerTokens([111, 222]);
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);
    bus.__emitPeerMessage({ t: 'want', from: 'other-tab', tokens: [222] });
    post.mockClear();

    offA();                       // this tab no longer wants either token
    await vi.advanceTimersByTimeAsync(200);

    const unsub = post.mock.calls.filter(([url]) => String(url).endsWith('/ticker/unsubscribe'));
    expect(unsub).toHaveLength(1);
    expect(unsub[0][1]).toEqual({ instrument_tokens: [111] });   // 222 stays
    offB();
  });

  it('serialises passes so a slow request cannot be overtaken', async () => {
    let release: () => void = () => {};
    post.mockImplementationOnce(() => new Promise((r) => { release = () => r({ ok: true }); }));
    const offA = mod.registerTokens([111]);
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);          // first pass now in flight
    const offB = mod.registerTokens([222]);
    await vi.advanceTimersByTimeAsync(100);
    expect(post).toHaveBeenCalledTimes(1);           // second pass waits

    release();
    await vi.advanceTimersByTimeAsync(200);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toEqual({ instrument_tokens: [222], mode: 'quote' });
    offA(); offB();
  });
});

describe('last consumer leaving', () => {
  it('still releases its tokens on the server', async () => {
    const off = mod.registerTokens([111]);
    await connectAndAuth();
    await vi.advanceTimersByTimeAsync(100);
    post.mockClear();

    off();
    await vi.advanceTimersByTimeAsync(200);

    expect(post).toHaveBeenCalledWith('/api/v1/kite/ticker/unsubscribe', { instrument_tokens: [111] });
  });
});

describe('account switch', () => {
  it('drops held ticks and re-asserts subscriptions against the new session', async () => {
    const off = mod.registerTokens([111]);
    const ws = await connectAndAuth();
    ws.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 7 }] });
    await vi.advanceTimersByTimeAsync(100);
    post.mockClear();

    mod.resetLiveTickState();

    expect(mod.getTick(111)).toBeUndefined();
    await vi.advanceTimersByTimeAsync(100);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/kite/ticker/subscribe', { instrument_tokens: [111], mode: 'quote' },
    );
    off();
  });
});

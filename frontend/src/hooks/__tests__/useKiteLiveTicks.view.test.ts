/**
 * The stream socket serves one tenant. In the Simulation view it must say so in its URL (a
 * browser WebSocket cannot send the view header), and a switch must drop the old socket and the
 * ticks it held rather than keep painting the other view's stream.
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
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;
  constructor(public url: string) { MockWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.closeCalls += 1; this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
  open() { this.readyState = 1; this.onopen?.(); }
  recv(frame: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(frame) }); }
  channels() { return this.sent.filter((m) => m.action === 'subscribe').map((m) => m.channel as string); }
}

type Mod = typeof import('../useKiteLiveTicks');
let mod: Mod;
let views: typeof import('../../store/useViewStore');
const last = () => MockWebSocket.instances[MockWebSocket.instances.length - 1];

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetModules();
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  sessionStorage.setItem('sterling.auth.access', 'jwt-for-test');
  vi.doMock('../../utils/api', () => ({ api: { post: vi.fn(async () => ({ ok: true })), get: vi.fn() } }));
  views = await import('../../store/useViewStore');
  mod = await import('../useKiteLiveTicks');
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.doUnmock('../../utils/api');
  sessionStorage.clear();
});

describe('the stream socket follows the view', () => {
  it('connects to the plain stream URL in the live view', () => {
    const off = mod.registerTokens([111]);
    expect(last().url).toMatch(/\/api\/v1\/stream\/ws$/);
    off();
  });

  it('adds ?view=simulation in the Simulation view, and still keeps the token out of the URL', () => {
    views.useViewStore.getState().setView('simulation');
    const off = mod.registerTokens([111]);
    expect(last().url).toMatch(/\/api\/v1\/stream\/ws\?view=simulation$/);
    expect(last().url).not.toContain('jwt-for-test');
    off();
  });

  it('on a switch drops the old socket and the ticks it held, and reconnects for the new view', async () => {
    const off = mod.registerTokens([111]);
    const first = last();
    first.open();
    first.recv({ type: 'auth_ok', user_id: 'default' });
    first.recv({ type: 'kite_ticks', ticks: [{ instrument_token: 111, last_price: 100 }] });
    expect(mod.getTick(111)?.last_price).toBe(100);

    views.useViewStore.getState().setView('simulation');
    await vi.advanceTimersByTimeAsync(0);

    expect(first.closeCalls).toBe(1);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(last().url).toMatch(/\?view=simulation$/);
    expect(mod.getTick(111)).toBeUndefined();

    last().open();
    last().recv({ type: 'auth_ok', user_id: 'sim:default' });
    expect(last().channels()).toEqual(['kite_ticks:sim:default', 'kite_orders:sim:default']);
    off();
  });

  it('does nothing on a switch while no screen is listening', async () => {
    views.useViewStore.getState().setView('simulation');
    await vi.advanceTimersByTimeAsync(0);
    expect(MockWebSocket.instances).toHaveLength(0);
  });
});

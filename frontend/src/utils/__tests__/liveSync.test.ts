import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  __resetLiveSync,
  createResyncCoalescer,
  isResyncKey,
  isStateChangingWrite,
  notifyWriteSettled,
  onResync,
  requestResync,
  resyncQueries,
} from '../liveSync';

afterEach(() => {
  __resetLiveSync();
  vi.useRealTimers();
});

describe('which queries a resync refetches', () => {
  it('covers positions, orders, kill switch, daily loss and session status', () => {
    for (const key of [
      'kite-positions', 'kite-orders', 'kite-kill-switch', 'kite-daily-loss', 'kite-status',
      'kite-engine-open-positions', 'kite-engine-config', 'snapback-snapshot',
      'gamma-move-snapshot', 'intraday-positions',
    ]) {
      expect(isResyncKey([key, 'anything']), key).toBe(true);
    }
  });

  it('leaves static and heavy data alone', () => {
    for (const key of ['kite-historical', 'kite-instruments', 'kite-instrument-lots', 'candles', 'unified-backtest-presets']) {
      expect(isResyncKey([key]), key).toBe(false);
    }
    expect(isResyncKey([])).toBe(false);
    expect(isResyncKey([42 as unknown as string])).toBe(false);
  });

  it('invalidates only matching queries', async () => {
    const qc = new QueryClient();
    qc.setQueryData(['kite-positions'], { net: [] });
    qc.setQueryData(['kite-historical', 1], [1, 2, 3]);
    await resyncQueries(qc);
    expect(qc.getQueryState(['kite-positions'])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['kite-historical', 1])?.isInvalidated).toBe(false);
  });
});

describe('write notifications', () => {
  it('treats order, exit and kill-switch writes as state changes', () => {
    expect(isStateChangingWrite('POST', '/api/v1/kite/orders')).toBe(true);
    expect(isStateChangingWrite('DELETE', '/api/v1/kite/orders/123?variety=regular')).toBe(true);
    expect(isStateChangingWrite('POST', '/api/v1/kite/engine/emergency-halt')).toBe(true);
    expect(isStateChangingWrite('DELETE', '/api/v1/kite/engine/open-positions/NIFTY')).toBe(true);
    expect(isStateChangingWrite('PUT', '/api/v1/kite/risk/daily-loss')).toBe(true);
    expect(isStateChangingWrite('POST', '/api/v1/kite/session')).toBe(true);
  });

  it('ignores reads and computations that never touch broker state', () => {
    expect(isStateChangingWrite('GET', '/api/v1/kite/orders')).toBe(false);
    expect(isStateChangingWrite('POST', '/api/v1/kite/margins/orders')).toBe(false);
    expect(isStateChangingWrite('POST', '/api/v1/kite/charges/orders')).toBe(false);
    expect(isStateChangingWrite('POST', '/api/v1/kite/ticker/subscribe')).toBe(false);
    expect(isStateChangingWrite('POST', '/api/v1/kite/chart-state/NIFTY')).toBe(false);
    expect(isStateChangingWrite('POST', '/api/v1/auth/refresh')).toBe(false);
  });

  it('requests a resync for a write and not for a margin quote', () => {
    const seen: string[] = [];
    onResync((r) => seen.push(r));
    notifyWriteSettled('POST', '/api/v1/kite/margins/orders');
    expect(seen).toEqual([]);
    notifyWriteSettled('POST', '/api/v1/kite/orders');
    expect(seen).toEqual(['write']);
  });
});

describe('the in-tab resync bus', () => {
  it('delivers to every listener even when one throws', () => {
    const seen: string[] = [];
    onResync(() => { throw new Error('boom'); });
    onResync((r) => seen.push(r));
    requestResync('online');
    expect(seen).toEqual(['online']);
  });

  it('unsubscribes', () => {
    const seen: string[] = [];
    const off = onResync((r) => seen.push(r));
    off();
    requestResync('online');
    expect(seen).toEqual([]);
  });
});

describe('resync coalescing', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-05T09:30:00Z')); });

  it('collapses a reconnect/online/visible burst into one run', () => {
    const run = vi.fn();
    const c = createResyncCoalescer(run, { debounceMs: 200, minGapMs: 1000 });
    c.request('ws-reconnect');
    c.request('online');
    c.request('visible');
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith(['ws-reconnect', 'online', 'visible']);
  });

  it('enforces a minimum gap so a flapping socket cannot storm the API', () => {
    const run = vi.fn();
    const c = createResyncCoalescer(run, { debounceMs: 100, minGapMs: 1500 });
    c.request('ws-reconnect');
    vi.advanceTimersByTime(100);
    expect(run).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(300);
    c.request('ws-reconnect');       // 400ms after the last run
    vi.advanceTimersByTime(900);
    expect(run).toHaveBeenCalledTimes(1);   // still inside the 1500ms floor
    vi.advanceTimersByTime(300);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('cancel drops queued work', () => {
    const run = vi.fn();
    const c = createResyncCoalescer(run);
    c.request('online');
    c.cancel();
    vi.advanceTimersByTime(5000);
    expect(run).not.toHaveBeenCalled();
  });
});

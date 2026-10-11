import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CONNECTING_GRACE_MS, STALE_AFTER_MS, deriveFeedHealth, useStreamHealthStore,
  type FeedHealthInput,
} from '../useStreamHealthStore';

const NOW = 1_000_000;
const base = (over: Partial<FeedHealthInput> = {}): FeedHealthInput => ({
  transport: 'open', transportSince: NOW - 60_000, lastActivityAt: NOW - 1_000,
  broker: null, now: NOW, ...over,
});

describe('deriveFeedHealth', () => {
  it('stays silent when nothing is using the stream', () => {
    expect(deriveFeedHealth(base({ transport: 'idle' })).level).toBe('idle');
  });

  it('reports a healthy socket as live', () => {
    expect(deriveFeedHealth(base()).level).toBe('live');
  });

  it('a quiet market is live, not stale: heartbeats keep activity fresh', () => {
    // No tick for minutes, but the server heartbeat landed 5s ago.
    expect(deriveFeedHealth(base({ lastActivityAt: NOW - 5_000 })).level).toBe('live');
  });

  it('flags a socket that is open but silent as stale', () => {
    const h = deriveFeedHealth(base({ lastActivityAt: NOW - (STALE_AFTER_MS + 3_000) }));
    expect(h.level).toBe('stale');
    expect(h.label).toMatch(/^STALE \d+s$/);
  });

  it('flags a closed socket as down and says the screen is not live', () => {
    const h = deriveFeedHealth(base({ transport: 'closed' }));
    expect(h.level).toBe('down');
    expect(h.detail).toMatch(/not live/i);
  });

  it('gives a fresh connect a grace period before calling it down', () => {
    const fresh = deriveFeedHealth(base({ transport: 'connecting', transportSince: NOW - 1_000 }));
    expect(fresh.level).toBe('connecting');
    const slow = deriveFeedHealth(base({
      transport: 'connecting', transportSince: NOW - (CONNECTING_GRACE_MS + 1_000),
    }));
    expect(slow.level).toBe('down');
  });

  it('allows a brief reconnect without an offline alarm', () => {
    const h = deriveFeedHealth(base({ transport: 'closed', interruptionSince: NOW - 100 }));
    expect(h.level).toBe('connecting');
    expect(h.label).toBe('RECONNECTING…');
  });

  it('keeps the outage deadline across connection attempts', () => {
    expect(deriveFeedHealth(base({ transport: 'connecting', transportSince: NOW - 100,
      interruptionSince: NOW - CONNECTING_GRACE_MS - 100 })).level).toBe('down');
  });

  it('reports the broker feed down while the browser socket is fine', () => {
    const h = deriveFeedHealth(base({
      broker: { started: true, connected: false, lastTickMs: 5, lastError: 'ConnectionClosed', reportedAt: NOW },
    }));
    expect(h.level).toBe('broker-down');
    expect(h.detail).toContain('ConnectionClosed');
  });

  it('does not alarm when the backend simply has no ticker (paper / not logged in)', () => {
    const h = deriveFeedHealth(base({
      broker: { started: false, connected: false, lastTickMs: 0, lastError: null, reportedAt: NOW },
    }));
    expect(h.level).toBe('live');
  });

  it('socket silence outranks a healthy broker report', () => {
    const h = deriveFeedHealth(base({
      lastActivityAt: NOW - 60_000,
      broker: { started: true, connected: true, lastTickMs: 1, lastError: null, reportedAt: NOW - 60_000 },
    }));
    expect(h.level).toBe('stale');
  });
});

describe('useStreamHealthStore', () => {
  beforeEach(() => useStreamHealthStore.getState().reset());

  it('counts a reconnect only after the socket has actually been closed', () => {
    const s = useStreamHealthStore.getState();
    s.setTransport('connecting');
    s.setTransport('open');
    expect(useStreamHealthStore.getState().reconnects).toBe(0);
    s.setTransport('closed');
    s.setTransport('connecting');
    s.setTransport('open');
    expect(useStreamHealthStore.getState().reconnects).toBe(1);
  });

  it('retains interruption start until recovery', () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const s = useStreamHealthStore.getState();
    s.setTransport('open');
    s.setTransport('closed');
    clock.mockReturnValue(NOW + 2_000);
    s.setTransport('connecting');
    expect(useStreamHealthStore.getState().interruptionSince).toBe(NOW);
    s.setTransport('closed');
    expect(useStreamHealthStore.getState().interruptionSince).toBe(NOW);
    s.setTransport('open');
    expect(useStreamHealthStore.getState().interruptionSince).toBe(0);
    clock.mockRestore();
  });

  it('ignores a repeated transition', () => {
    const s = useStreamHealthStore.getState();
    s.setTransport('open');
    const since = useStreamHealthStore.getState().transportSince;
    s.setTransport('open');
    expect(useStreamHealthStore.getState().transportSince).toBe(since);
  });

  it('reset forgets the broker report and boot id', () => {
    const s = useStreamHealthStore.getState();
    s.setBootId('abc');
    s.setBroker({ started: true, connected: true, lastTickMs: 1, lastError: null, reportedAt: 1 });
    s.reset();
    expect(useStreamHealthStore.getState().bootId).toBeNull();
    expect(useStreamHealthStore.getState().broker).toBeNull();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlaceGttBody } from '../../types/kite';

const gtt = (symbol = 'INFY'): PlaceGttBody => ({
  trigger_type: 'single', tradingsymbol: symbol, exchange: 'NSE',
  last_price: 1500, trigger_values: [1400], orders: [
    { tradingsymbol: symbol, exchange: 'NSE', transaction_type: 'SELL', quantity: 10, order_type: 'LIMIT', product: 'CNC', price: 1400 },
  ],
});

async function freshStore() {
  vi.resetModules();
  return import('../useKitePendingProtectionStore');
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('pending protection survives a reload', () => {
  it('restores entries in a fresh module instance', async () => {
    const first = await freshStore();
    first.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });

    const reloaded = await freshStore();
    expect(reloaded.useKitePendingProtectionStore.getState().pending).toEqual([
      { orderId: 'o1', gtt: gtt() },
    ]);
  });

  it('drops entries older than a trading day when it hydrates', async () => {
    const m = await freshStore();
    m.useKitePendingProtectionStore.getState().add({ orderId: 'old', gtt: gtt() });
    const stored = JSON.parse(localStorage.getItem('sterling.kite.pending-protection.v1')!);
    stored[0].savedAt = Date.now() - m.PENDING_PROTECTION_TTL_MS - 1_000;
    localStorage.setItem('sterling.kite.pending-protection.v1', JSON.stringify(stored));

    const reloaded = await freshStore();
    expect(reloaded.useKitePendingProtectionStore.getState().pending).toEqual([]);
  });

  it('ignores corrupt storage instead of crashing the app on load', async () => {
    localStorage.setItem('sterling.kite.pending-protection.v1', '{not json');
    const m = await freshStore();
    expect(m.useKitePendingProtectionStore.getState().pending).toEqual([]);
  });
});

describe('bounds', () => {
  it('replaces a duplicate order id instead of growing', async () => {
    const m = await freshStore();
    const s = m.useKitePendingProtectionStore.getState();
    s.add({ orderId: 'o1', gtt: gtt('A') });
    s.add({ orderId: 'o1', gtt: gtt('B') });
    const pending = m.useKitePendingProtectionStore.getState().pending;
    expect(pending).toHaveLength(1);
    expect(pending[0].gtt.tradingsymbol).toBe('B');
  });

  it('caps the list, keeping the newest', async () => {
    const m = await freshStore();
    const s = m.useKitePendingProtectionStore.getState();
    for (let i = 0; i < m.MAX_PENDING_PROTECTIONS + 10; i++) s.add({ orderId: `o${i}`, gtt: gtt() });
    const pending = m.useKitePendingProtectionStore.getState().pending;
    expect(pending).toHaveLength(m.MAX_PENDING_PROTECTIONS);
    expect(pending[pending.length - 1].orderId).toBe(`o${m.MAX_PENDING_PROTECTIONS + 9}`);
  });

  it('clear() empties memory and storage', async () => {
    const m = await freshStore();
    m.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });
    m.useKitePendingProtectionStore.getState().clear();
    expect(m.useKitePendingProtectionStore.getState().pending).toEqual([]);
    expect(JSON.parse(localStorage.getItem('sterling.kite.pending-protection.v1')!)).toEqual([]);
  });
});

describe('exactly-once firing across tabs', () => {
  it('hands the entry to the first claimant only', async () => {
    const m = await freshStore();
    m.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });

    const [a, b] = await Promise.all([
      m.claimPendingProtection('o1'),
      m.claimPendingProtection('o1'),
    ]);

    expect([a, b].filter(Boolean)).toHaveLength(1);
    expect(m.useKitePendingProtectionStore.getState().pending).toEqual([]);
  });

  it('a second tab loses the claim even though its memory still holds the entry', async () => {
    // Tab A and tab B each hydrated the same entry.
    const tabA = await freshStore();
    tabA.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });
    const tabB = await freshStore();
    expect(tabB.useKitePendingProtectionStore.getState().pending).toHaveLength(1);

    const winner = await tabA.claimPendingProtection('o1');
    const loser = await tabB.claimPendingProtection('o1');

    expect(winner).toEqual({ orderId: 'o1', gtt: gtt() });
    expect(loser).toBeNull();
    expect(tabB.useKitePendingProtectionStore.getState().pending).toEqual([]);
  });

  it('respects a held Web Lock: a tab that cannot get it does not fire', async () => {
    const m = await freshStore();
    m.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });
    vi.stubGlobal('navigator', {
      ...navigator,
      locks: {
        request: async (_name: string, _opts: unknown, cb: (l: unknown) => unknown) => cb(null),
      },
    });

    expect(await m.claimPendingProtection('o1')).toBeNull();
    // Untouched: the lock holder will fire it.
    expect(m.useKitePendingProtectionStore.getState().pending).toHaveLength(1);
  });

  it('takes the entry when the Web Lock is granted', async () => {
    const m = await freshStore();
    m.useKitePendingProtectionStore.getState().add({ orderId: 'o1', gtt: gtt() });
    vi.stubGlobal('navigator', {
      ...navigator,
      locks: {
        request: async (_name: string, _opts: unknown, cb: (l: unknown) => unknown) => cb({}),
      },
    });
    expect(await m.claimPendingProtection('o1')).toEqual({ orderId: 'o1', gtt: gtt() });
  });

  it('claiming an unknown order is a quiet no-op', async () => {
    const m = await freshStore();
    expect(await m.claimPendingProtection('nope')).toBeNull();
  });
});

describe('cross-tab sync', () => {
  it('adopts another tab\'s change from the storage event', async () => {
    const m = await freshStore();
    localStorage.setItem('sterling.kite.pending-protection.v1', JSON.stringify([
      { orderId: 'from-other-tab', gtt: gtt(), savedAt: Date.now() },
    ]));
    window.dispatchEvent(new StorageEvent('storage', { key: 'sterling.kite.pending-protection.v1' }));
    expect(m.useKitePendingProtectionStore.getState().pending.map((p) => p.orderId))
      .toEqual(['from-other-tab']);
  });
});

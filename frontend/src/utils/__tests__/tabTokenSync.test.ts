import { describe, expect, it } from 'vitest';
import { PeerTokenRegistry } from '../tabTokenSync';

describe('PeerTokenRegistry', () => {
  it('reports tokens another tab still wants', () => {
    const r = new PeerTokenRegistry();
    r.update('tab-b', [111, 222]);
    expect(r.wantedByPeers(111)).toBe(true);
    expect(r.wantedByPeers(333)).toBe(false);
  });

  it('a later announcement replaces the earlier one', () => {
    const r = new PeerTokenRegistry();
    r.update('tab-b', [111]);
    r.update('tab-b', [222]);
    expect(r.wantedByPeers(111)).toBe(false);
    expect(r.wantedByPeers(222)).toBe(true);
  });

  it('forgets a tab that said goodbye', () => {
    const r = new PeerTokenRegistry();
    r.update('tab-b', [111]);
    r.remove('tab-b');
    expect(r.wantedByPeers(111)).toBe(false);
    expect(r.size).toBe(0);
  });

  it('expires a peer that stopped announcing (crashed tab)', () => {
    let now = 1_000;
    const r = new PeerTokenRegistry(10_000, () => now);
    r.update('tab-b', [111]);
    now += 9_000;
    expect(r.wantedByPeers(111)).toBe(true);
    now += 2_000;
    expect(r.wantedByPeers(111)).toBe(false);
    expect(r.size).toBe(0);
  });

  it('does not mistake a throttled background tab for a dead one', () => {
    let now = 0;
    const r = new PeerTokenRegistry(undefined, () => now);
    r.update('tab-b', [111]);
    now += 60_000;   // hidden tabs may tick only once a minute
    expect(r.wantedByPeers(111)).toBe(true);
  });
});

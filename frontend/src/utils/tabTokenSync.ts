/**
 * Which instrument tokens OTHER tabs of this browser still want streamed.
 *
 * The backend keeps one subscription set per user, not per tab. Without this a
 * tab that closes its chart sends `ticker/unsubscribe` and silently blinds every
 * other tab (and their charts) that still shows the same instrument: their
 * socket stays open and healthy, the ticks just stop.
 */
export class PeerTokenRegistry {
  private peers = new Map<string, { tokens: Set<number>; at: number }>();

  /**
   * `ttlMs` is generous on purpose: a hidden tab's timers are throttled to about
   * once a minute, and a peer that is merely throttled must not be mistaken for
   * a peer that is gone.
   */
  constructor(
    private readonly ttlMs: number = 150_000,
    private readonly clock: () => number = Date.now,
  ) {}

  update(tabId: string, tokens: Iterable<number>): void {
    this.peers.set(tabId, { tokens: new Set(tokens), at: this.clock() });
  }

  remove(tabId: string): void {
    this.peers.delete(tabId);
  }

  wantedByPeers(token: number): boolean {
    const now = this.clock();
    for (const [id, peer] of this.peers) {
      if (now - peer.at > this.ttlMs) {
        this.peers.delete(id);
        continue;
      }
      if (peer.tokens.has(token)) return true;
    }
    return false;
  }

  get size(): number {
    return this.peers.size;
  }
}

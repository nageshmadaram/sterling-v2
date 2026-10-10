/**
 * Live-state synchronisation plumbing shared by the stream socket, the API layer
 * and the React Query client.
 *
 * Why this exists: polling queries keep painting their last result when the
 * backend restarts, the network drops, or the laptop wakes up, and that result
 * looks exactly like live data. Anything that can leave the screen behind the
 * broker -- a socket reconnect, a backend restart, an account switch, a write
 * whose outcome we did not see -- calls `requestResync()`, and a single listener
 * (see `useLiveResync`) refetches the trading-state queries.
 *
 * Deliberately dependency-free (no React, no API client) so every layer can
 * import it without creating a cycle.
 */
import type { QueryClient } from '@tanstack/react-query';

export type ResyncReason =
  | 'ws-reconnect'      // the stream socket came back after being down
  | 'boot-change'       // the backend process restarted (boot_id changed)
  | 'broker-reconnect'  // the backend's Kite socket came back
  | 'online'            // the browser regained network
  | 'visible'           // the tab came back to the foreground
  | 'account-change'    // active Kite account switched / logged out
  | 'session'           // Kite session (re)established
  | 'write'             // a state-changing request finished (success OR failure)
  | 'remote';           // another tab of this browser asked for it

/**
 * First element of the query keys that hold trading state. Everything here is
 * safe to refetch at any time. Static/heavy data (instrument search, historical
 * candles, backtests) is deliberately absent.
 */
export const RESYNC_QUERY_PREFIXES: ReadonlySet<string> = new Set([
  // broker session + account
  'kite-status', 'kite-accounts', 'kite-diagnostics-summary', 'kite-ticker-status',
  // money state
  'kite-positions', 'kite-orders', 'kite-trades', 'kite-margins', 'kite-holdings', 'kite-gtt',
  // risk switches
  'kite-daily-loss', 'kite-kill-switch',
  // engine state
  'kite-engine-open-positions', 'kite-engine-activity', 'kite-engine-config',
  'kite-engine-readiness', 'kite-engine-signals',
  // the three live strategies + family operations
  'snapback-config', 'snapback-snapshot', 'snapback-family-operations',
  'gamma-move-config', 'gamma-move-snapshot', 'price-rules',
  'intraday-config', 'intraday-snapshot', 'intraday-positions',
  'family-operations-v2',
  'adaptive-edge-engine-positions', 'adaptive-edge-engine-snapshot',
]);

export function isResyncKey(queryKey: readonly unknown[]): boolean {
  const head = queryKey?.[0];
  return typeof head === 'string' && RESYNC_QUERY_PREFIXES.has(head);
}

/** Refetch every active trading-state query now; mark inactive ones stale. */
export function resyncQueries(qc: QueryClient): Promise<void> {
  return qc.invalidateQueries({ predicate: (q) => isResyncKey(q.queryKey) });
}

// ─── in-tab bus ──────────────────────────────────────────────────────────────
type ResyncListener = (reason: ResyncReason) => void;
const _resyncListeners = new Set<ResyncListener>();

export function onResync(listener: ResyncListener): () => void {
  _resyncListeners.add(listener);
  return () => { _resyncListeners.delete(listener); };
}

export function requestResync(reason: ResyncReason): void {
  for (const listener of [..._resyncListeners]) {
    try { listener(reason); } catch { /* one bad listener must not stop the rest */ }
  }
}

/**
 * Collapse bursts of resync requests into one run.
 *
 * Leading-edge debounce (`debounceMs`) so a reconnect + visibility + online
 * trio arriving together refetches once, and a floor of `minGapMs` between
 * runs so a flapping socket cannot turn into a request storm.
 */
export function createResyncCoalescer(
  run: (reasons: ResyncReason[]) => void,
  { debounceMs = 250, minGapMs = 1_500 }: { debounceMs?: number; minGapMs?: number } = {},
): { request: (reason: ResyncReason) => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastRunAt = 0;
  let pending: ResyncReason[] = [];

  const fire = () => {
    timer = null;
    const reasons = pending;
    pending = [];
    lastRunAt = Date.now();
    if (reasons.length) run(reasons);
  };

  return {
    request(reason) {
      pending.push(reason);
      if (timer) return;
      const sinceLast = Date.now() - lastRunAt;
      const wait = Math.max(debounceMs, lastRunAt ? minGapMs - sinceLast : 0);
      timer = setTimeout(fire, wait);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
      pending = [];
    },
  };
}

// ─── API write notifications ─────────────────────────────────────────────────
/**
 * Requests that read or compute but never change broker/engine state. A margin
 * quote fired on every keystroke in the order ticket must not refetch the book.
 */
const NON_STATE_WRITE_PATHS = [
  '/margins', '/charges', '/ticker/', '/chart-state', '/backtest', '/watchlist',
  '/drawings', '/instruments', '/auth/', '/test', '/login-url', '/replay', '/simulation',
];

export function isStateChangingWrite(method: string, path: string): boolean {
  if (method.toUpperCase() === 'GET') return false;
  const p = path.split('?')[0];
  return !NON_STATE_WRITE_PATHS.some((fragment) => p.includes(fragment));
}

/**
 * Called by the API layer after every non-GET request settles -- including the
 * ones that failed. A timed-out order POST may well have reached the broker, so
 * "it errored" is not "nothing happened"; the screen must re-read the book.
 */
export function notifyWriteSettled(method: string, path: string): void {
  if (!isStateChangingWrite(method, path)) return;
  requestResync('write');
  // Other tabs of this browser are showing the same book; tell them too.
  postToPeers({ t: 'resync', from: TAB_ID });
}

// ─── auth expiry ─────────────────────────────────────────────────────────────
export const AUTH_EXPIRED_EVENT = 'sterling:auth-expired';

export function notifyAuthExpired(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}

// ─── cross-tab channel ───────────────────────────────────────────────────────
export type PeerMessage =
  | { t: 'resync'; from: string }
  | { t: 'want'; from: string; tokens: number[] }
  | { t: 'hello'; from: string }
  | { t: 'bye'; from: string };

/** Identifies this tab on the cross-tab channel. */
export const TAB_ID: string =
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

const CHANNEL_NAME = 'sterling-live-sync';
let _channel: BroadcastChannel | null = null;
let _channelTried = false;
const _peerListeners = new Set<(m: PeerMessage) => void>();

function channel(): BroadcastChannel | null {
  if (_channelTried) return _channel;
  _channelTried = true;
  try {
    if (typeof BroadcastChannel === 'undefined') return null;
    _channel = new BroadcastChannel(CHANNEL_NAME);
    _channel.onmessage = (event: MessageEvent) => {
      const m = event.data as PeerMessage | null;
      if (!m || typeof m !== 'object' || m.from === TAB_ID) return;
      for (const listener of [..._peerListeners]) {
        try { listener(m); } catch { /* isolate listeners */ }
      }
    };
  } catch {
    _channel = null;
  }
  return _channel;
}

export function postToPeers(message: PeerMessage): void {
  try { channel()?.postMessage(message); } catch { /* closed channel: single-tab behaviour */ }
}

export function onPeerMessage(listener: (m: PeerMessage) => void): () => void {
  channel();
  _peerListeners.add(listener);
  return () => { _peerListeners.delete(listener); };
}

/** Test hook: deliver a message as if another tab had posted it. */
export function __emitPeerMessage(message: PeerMessage): void {
  for (const listener of [..._peerListeners]) listener(message);
}

/** Test hook: drop all listeners and the channel. */
export function __resetLiveSync(): void {
  _resyncListeners.clear();
  _peerListeners.clear();
  try { _channel?.close(); } catch { /* ignore */ }
  _channel = null;
  _channelTried = false;
}

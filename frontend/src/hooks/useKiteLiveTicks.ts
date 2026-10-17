/**
 * useKiteLiveTicks — module-level singleton consuming the Kite tick WebSocket.
 *
 * The backend already runs one KiteTicker per user and fans decoded ticks out to
 * the `kite_ticks:{userId}` channel over the shared `/api/v1/stream/ws` socket.
 * This module opens ONE socket per tab, stores token-indexed ticks, reconciles
 * the ref-counted token union requested by mounted consumers, and carries the
 * per-user order channel (`kite_orders:{userId}`) on the same connection.
 *
 * It is also where "is the screen live?" is decided. Anything that can leave the
 * UI behind the broker -- a dropped or silent socket, a backend restart, the
 * broker feed going down -- is surfaced through `useStreamHealthStore` and
 * triggers `requestResync()` so trading-state queries refetch.
 */
import { useCallback, useSyncExternalStore } from 'react';
import { api } from '../utils/api';
import { getAccessToken, getRefreshToken, refreshSterlingAccess } from '../utils/auth';
import {
  TAB_ID, onPeerMessage, postToPeers, requestResync, notifyAuthExpired,
} from '../utils/liveSync';
import { PeerTokenRegistry } from '../utils/tabTokenSync';
import { useStreamHealthStore } from '../store/useStreamHealthStore';
import { useViewStore, withViewQuery } from '../store/useViewStore';

const K = '/api/v1/kite';
const STREAM_WS_PATH = '/api/v1/stream/ws';
const BASE_DELAY = 1_000;
const MAX_DELAY = 10_000;
const BACKOFF_JITTER = 0.3;          // +/-30%: tabs must not reconnect in lock-step
const RECONCILE_DEBOUNCE = 50;
const RECONCILE_RETRY_BASE = 2_000;
const RECONCILE_RETRY_MAX = 15_000;
const UI_NOTIFY_MS = 50;
const INTERACTION_GRACE_MS = 50;
/** No frame at all (not even a server heartbeat) for this long: the socket is dead. */
export const WATCHDOG_STALE_MS = 30_000;
const WATCHDOG_INTERVAL_MS = 2_000;
/** A tick older than this loses to the REST snapshot instead of overriding it. */
export const TICK_TRUST_MS = 15_000;
/** Re-assert the whole subscription set this often; cheap and self-healing. */
const RESUBSCRIBE_ALL_MS = 60_000;
const PEER_ANNOUNCE_MS = 5_000;
/** Do not rebuild the server-side ticker more often than this. */
const REBUILD_MIN_GAP_MS = 5_000;

type BrowserLocation = Pick<Location, 'protocol' | 'host'>;

export function resolveKiteStreamWsUrl(
  apiBase: string | undefined = import.meta.env.VITE_API_BASE_URL as string | undefined,
  locationLike: BrowserLocation | undefined = typeof window !== 'undefined' ? window.location : undefined,
): string {
  const base = (apiBase ?? '').trim().replace(/\/+$/, '');
  const target = `${base}${STREAM_WS_PATH}`;
  if (/^wss?:\/\//i.test(target)) return target;
  if (/^https?:\/\//i.test(target)) {
    const url = new URL(target);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }
  if (locationLike) {
    const protocol = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
    const path = target.startsWith('/') ? target : `/${target}`;
    return `${protocol}//${locationLike.host}${path}`;
  }
  const path = target.startsWith('/') ? target : `/${target}`;
  return `ws://localhost:8000${path}`;
}

/** Reconnect delay for the Nth consecutive failed attempt (0-based), with jitter. */
export function backoffDelay(attempt: number, rand: () => number = Math.random): number {
  const exp = Math.min(MAX_DELAY, BASE_DELAY * 2 ** Math.max(0, attempt));
  const factor = 1 - BACKOFF_JITTER + rand() * 2 * BACKOFF_JITTER;
  return Math.max(1, Math.round(exp * factor));
}

export interface KiteTick {
  instrument_token: number;
  last_price?: number;
  change?: number;
  ohlc?: { open?: number; high?: number; low?: number; close?: number };
  oi?: number;
  depth?: unknown;
  [k: string]: unknown;
}

const _tickByToken = new Map<number, KiteTick>();
const _tickReceivedAt = new Map<number, number>();
const _tokenVersions = new Map<number, number>();
let _feedEpoch = 0;
let _version = 0;
const _storeListeners = new Set<() => void>();
let _notifyScheduled = false;
let _lastInteractionAt = 0;
let _interactionListenersInstalled = false;

function markInteraction() {
  _lastInteractionAt = Date.now();
}

function installInteractionListeners() {
  if (_interactionListenersInstalled || typeof window === 'undefined') return;
  _interactionListenersInstalled = true;
  const opts: AddEventListenerOptions = { passive: true, capture: true };
  window.addEventListener('pointerdown', markInteraction, opts);
  window.addEventListener('pointermove', markInteraction, opts);
  window.addEventListener('wheel', markInteraction, opts);
  window.addEventListener('scroll', markInteraction, opts);
  window.addEventListener('keydown', markInteraction, { capture: true });
}

installInteractionListeners();

function sameOhlc(a?: KiteTick['ohlc'], b?: KiteTick['ohlc']): boolean {
  return a?.open === b?.open && a?.high === b?.high && a?.low === b?.low && a?.close === b?.close;
}

function sameVisibleTick(previous: KiteTick | undefined, next: KiteTick): boolean {
  if (!previous) return false;
  return previous.last_price === next.last_price
    && previous.change === next.change
    && previous.oi === next.oi
    && sameOhlc(previous.ohlc, next.ohlc)
    && previous.depth === next.depth;
}

function flushStore() {
  const interactionAge = Date.now() - _lastInteractionAt;
  if (interactionAge < INTERACTION_GRACE_MS) {
    window.setTimeout(flushStore, INTERACTION_GRACE_MS - interactionAge);
    return;
  }
  _notifyScheduled = false;
  _version += 1;
  _storeListeners.forEach((listener) => listener());
}

function _notify() {
  if (_notifyScheduled) return;
  _notifyScheduled = true;
  window.setTimeout(flushStore, UI_NOTIFY_MS);
}

/**
 * The latest tick for a token, or undefined when the feed is not current.
 *
 * Callers overlay ticks on a REST snapshot ("tick wins"). A tick that outlives a
 * dead feed would therefore beat fresher REST data and freeze the price on screen
 * while looking live, so a feed that has gone quiet stops being trusted and the
 * REST snapshot (polled every few seconds in that state) takes over.
 */
export function getTick(token: number): KiteTick | undefined {
  if (!tickFeedTrusted() || Date.now() - (_tickReceivedAt.get(token) ?? 0) > TICK_TRUST_MS) return undefined;
  return _tickByToken.get(token);
}

function tickFeedTrusted(now: number = Date.now()): boolean {
  return _lastFrameAt > 0 && now - _lastFrameAt <= TICK_TRUST_MS;
}

const _desired = new Map<number, number>();
const _desiredFull = new Map<number, number>();
const _subscribed = new Map<number, 'quote' | 'full'>();
let _reconcileTimer: ReturnType<typeof setTimeout> | null = null;
let _reconcileRetryTimer: ReturnType<typeof setTimeout> | null = null;
let _reconcileFailures = 0;
let _reconciling = false;
let _reconcileAgain = false;

/** Tokens other tabs of this browser still want (see tabTokenSync). */
const _peers = new PeerTokenRegistry();

function _wantMode(token: number): 'quote' | 'full' {
  return (_desiredFull.get(token) ?? 0) > 0 ? 'full' : 'quote';
}

function _announceWants() {
  postToPeers({ t: 'want', from: TAB_ID, tokens: [..._desired.keys()] });
}

function _scheduleReconcile() {
  if (_reconcileTimer) return;
  _reconcileTimer = setTimeout(() => {
    _reconcileTimer = null;
    void _reconcile();
  }, RECONCILE_DEBOUNCE);
}

function _scheduleReconcileRetry() {
  if (_reconcileRetryTimer || _refCount <= 0) return;
  const delay = Math.min(RECONCILE_RETRY_MAX, RECONCILE_RETRY_BASE * 2 ** Math.min(Math.max(0, _reconcileFailures - 1), 6));
  _reconcileRetryTimer = setTimeout(() => {
    _reconcileRetryTimer = null;
    _scheduleReconcile();
  }, delay);
}

async function _reconcile() {
  // One pass at a time: two overlapping passes used to roll each other's
  // optimistic bookkeeping back when one of them failed.
  if (_reconciling) {
    _reconcileAgain = true;
    return;
  }
  _reconciling = true;
  try {
    await _reconcileOnce();
  } finally {
    _reconciling = false;
    if (_reconcileAgain) {
      _reconcileAgain = false;
      _scheduleReconcile();
    }
  }
}

async function _reconcileOnce() {
  const wanted = new Set<number>();
  for (const [token, count] of _desired) if (count > 0) wanted.add(token);
  _announceWants();

  const toSubscribe: number[] = [];
  for (const token of wanted) {
    if (_subscribed.get(token) !== _wantMode(token)) toSubscribe.push(token);
  }
  const notWanted = [..._subscribed.keys()].filter((token) => !wanted.has(token));
  // The backend holds one subscription set per user. Dropping a token another
  // tab is still showing would blind that tab while its socket looks healthy.
  const toRemove = notWanted.filter((token) => !_peers.wantedByPeers(token));
  if (!toSubscribe.length && !notWanted.length) return;

  const previous = new Map(_subscribed);
  toSubscribe.forEach((token) => _subscribed.set(token, _wantMode(token)));
  notWanted.forEach((token) => _subscribed.delete(token));
  if (!toSubscribe.length && !toRemove.length) return;

  const byMode: Record<'quote' | 'full', number[]> = { quote: [], full: [] };
  for (const token of toSubscribe) byMode[_wantMode(token)].push(token);

  try {
    if (byMode.full.length) {
      await api.post(`${K}/ticker/subscribe`, { instrument_tokens: byMode.full, mode: 'full' });
    }
    if (byMode.quote.length) {
      await api.post(`${K}/ticker/subscribe`, { instrument_tokens: byMode.quote, mode: 'quote' });
    }
    if (toRemove.length) {
      await api.post(`${K}/ticker/unsubscribe`, { instrument_tokens: toRemove });
    }
    _reconcileFailures = 0;
  } catch {
    _subscribed.clear();
    for (const [token, mode] of previous) _subscribed.set(token, mode);
    // A failed POST used to be final: nothing retried it until the token set
    // changed again, so a transient 5xx left the board without ticks for good.
    _reconcileFailures += 1;
    _scheduleReconcileRetry();
  }
}

export function registerTokens(tokens: number[], mode: 'quote' | 'full' = 'quote'): () => void {
  if (!tokens.length) return () => {};
  for (const token of tokens) {
    _desired.set(token, (_desired.get(token) ?? 0) + 1);
    if (mode === 'full') _desiredFull.set(token, (_desiredFull.get(token) ?? 0) + 1);
  }
  _refConnect();
  _scheduleReconcile();

  return () => {
    for (const token of tokens) {
      const count = (_desired.get(token) ?? 0) - 1;
      if (count <= 0) _desired.delete(token);
      else _desired.set(token, count);

      if (mode === 'full') {
        const fullCount = (_desiredFull.get(token) ?? 0) - 1;
        if (fullCount <= 0) _desiredFull.delete(token);
        else _desiredFull.set(token, fullCount);
      }
    }
    _refDisconnect();
    _scheduleReconcile();
  };
}

// ─── order updates (same socket, `kite_orders:{userId}` channel) ──────────────
export type KiteOrderFrame = Record<string, unknown>;
type OrderListener = (order: KiteOrderFrame) => void;
const _orderListeners = new Set<OrderListener>();

/**
 * Receive live order-state changes. Holds the stream open while subscribed.
 *
 * This used to be a second, separate socket that never sent an auth frame, so in
 * production its subscribe was refused (`authentication_required`) and fills,
 * cancels and rejections never reached the screen.
 */
export function subscribeKiteOrderUpdates(listener: OrderListener): () => void {
  _orderListeners.add(listener);
  _refConnect();
  return () => {
    _orderListeners.delete(listener);
    _refDisconnect();
  };
}

function _dispatchOrder(order: KiteOrderFrame) {
  for (const listener of [..._orderListeners]) {
    try { listener(order); } catch { /* a bad listener must not drop the update for the rest */ }
  }
}

let _ws: WebSocket | null = null;
/**
 * When a tick frame last arrived.
 *
 * Exists so a frozen price is never silent. Every live price in the app falls
 * back to a 30-second REST poll when the stream stops, which looks exactly like
 * a quiet market — the difference matters enough on a board that places orders
 * that it should be on screen rather than in a status endpoint.
 */
let _lastFrameAt = 0;
/** When ANY server frame last arrived (ticks, heartbeats, status, orders). */
let _lastActivityAt = 0;
let _bootId: string | null = null;
let _everOpened = false;
let _lastRebuildAt = 0;

let _refCount = 0;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let _attempt = 0;
let _timers: ReturnType<typeof setInterval>[] = [];
let _lastTrusted = false;

function _scheduleReconnect() {
  if (_refCount <= 0 || _reconnectTimer) return;
  const delay = backoffDelay(_attempt);
  _attempt += 1;
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = null;
    _connect();
  }, delay);
}

/** Close the current socket and reconnect without waiting for its close event. */
function _forceReconnect() {
  const dead = _ws;
  _ws = null;
  _lastFrameAt = 0;
  useStreamHealthStore.getState().setTransport(_refCount > 0 ? 'closed' : 'idle');
  try { dead?.close(); } catch { /* already gone */ }
  _bump();
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  _attempt = 0;
  _connect();
}

function _bump() {
  _feedEpoch += 1; // connection/session reset invalidates all token snapshots
  _version += 1;
  _storeListeners.forEach((listener) => listener());
}

function _subscribeVerifiedChannel(socket: WebSocket, userId: string) {
  if (_ws !== socket || socket.readyState !== WebSocket.OPEN) return;
  _attempt = 0;
  socket.send(JSON.stringify({ action: 'subscribe', channel: `kite_ticks:${userId}` }));
  socket.send(JSON.stringify({ action: 'subscribe', channel: `kite_orders:${userId}` }));
  _subscribed.clear();
  _scheduleReconcile();
  if (_everOpened) {
    // Orders and fills that happened while we were away never reached us.
    requestResync('ws-reconnect');
  }
  _everOpened = true;
}

function _noteBootId(bootId: unknown) {
  if (typeof bootId !== 'string' || !bootId) return;
  const previous = _bootId;
  _bootId = bootId;
  useStreamHealthStore.getState().setBootId(bootId);
  if (previous && previous !== bootId) {
    // The backend process restarted: its in-memory tickers and subscriptions are
    // gone and anything we hold may predate the restart.
    _subscribed.clear();
    _scheduleReconcile();
    requestResync('boot-change');
  }
}

function _applyBrokerStatus(message: Record<string, unknown>) {
  const store = useStreamHealthStore.getState();
  const previous = store.broker;
  const started = !!message.started;
  const connected = !!message.connected;
  store.setBroker({
    started,
    connected,
    lastTickMs: Number(message.last_tick_ms) || 0,
    lastError: typeof message.last_error === 'string' ? message.last_error : null,
    reportedAt: Date.now(),
  });
  if (started && !connected) {
    // Ticks already held are no longer current; let REST take over immediately.
    _lastFrameAt = 0;
    _bump();
  }
  if (connected && previous && previous.started && !previous.connected) {
    // Order updates sent while the broker socket was down were lost.
    requestResync('broker-reconnect');
  }
  if (!started && _desired.size > 0 && Date.now() - _lastRebuildAt > REBUILD_MIN_GAP_MS) {
    // The server-side ticker is gone (account switch, logout, restart). Ask for
    // it back; the backend rebuilds it on the next subscribe.
    _lastRebuildAt = Date.now();
    _subscribed.clear();
    _scheduleReconcile();
  }
}

function _connect() {
  if (_ws || _refCount <= 0) return;
  let socket: WebSocket;
  const health = useStreamHealthStore.getState();
  try {
    // A browser socket cannot carry the view header, so the Simulation view rides in the URL.
    socket = new WebSocket(withViewQuery(resolveKiteStreamWsUrl()));
  } catch {
    health.setTransport('closed');
    _scheduleReconnect();
    return;
  }

  _ws = socket;
  health.setTransport('connecting');
  socket.onopen = () => {
    if (_ws !== socket) return;
    _lastActivityAt = Date.now();
    useStreamHealthStore.getState().setTransport('open');
    const token = getAccessToken();
    if (token) {
      // Browser WebSockets cannot set Authorization headers. Authenticate in the
      // first frame so bearer credentials never appear in the URL/access logs.
      socket.send(JSON.stringify({ action: 'auth', token }));
    } else if (getRefreshToken()) {
      void refreshSterlingAccess().then((refreshed) => {
        const fresh = getAccessToken();
        if (fresh && _ws === socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ action: 'auth', token: fresh }));
        } else {
          _subscribeVerifiedChannel(socket, 'default');
        }
      });
    } else {
      // Development/test compatibility. Production is behind LoginGate and the
      // backend refuses subscriptions until a verified auth frame arrives.
      _subscribeVerifiedChannel(socket, 'default');
    }
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (_ws !== socket) return;
      _lastActivityAt = Date.now();

      if (message.type === 'auth_ok') {
        const userId = String(message.user_id || '');
        if (!userId) {
          socket.close();
          return;
        }
        _subscribeVerifiedChannel(socket, userId);
        return;
      }
      if (message.error === 'unauthorized_channel') {
        void refreshSterlingAccess().then((refreshed) => {
          const fresh = getAccessToken();
          if (fresh && _ws === socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ action: 'auth', token: fresh }));
          } else {
            socket.close(4401, 'Unauthorized channel');
          }
        });
        return;
      }
      if (message.type === 'auth_error' || message.error === 'authentication_required') {
        socket.close(4401, 'Authentication required');
        return;
      }
      if (message.type === 'heartbeat') {
        _noteBootId(message.boot_id);
        return;
      }
      if (message.type === 'kite_stream_status') {
        _applyBrokerStatus(message);
        return;
      }
      if (message.type === 'kite_order_update') {
        if (message.order && typeof message.order === 'object') _dispatchOrder(message.order);
        return;
      }

      if (message.type !== 'kite_ticks' || !Array.isArray(message.ticks)) return;
      let changed = false;
      for (const tick of message.ticks as KiteTick[]) {
        const token = Number(tick?.instrument_token);
        if (!Number.isFinite(token) || token <= 0) continue;
        _tickReceivedAt.set(token, Date.now());
        const previous = _tickByToken.get(token);
        const next = { ...tick, instrument_token: token };
        if (sameVisibleTick(previous, next)) continue;
        _tickByToken.set(token, next);
        _tokenVersions.set(token, (_tokenVersions.get(token) ?? 0) + 1);
        changed = true;
      }
      // Stamped on every tick frame, changed or not: an unchanged frame still
      // proves the feed is alive, and a price that has not moved must not read
      // as a dead feed.
      _lastFrameAt = Date.now();
      if (changed) _notify();
    } catch {
      // Ignore unrelated or malformed stream frames.
    }
  };

  socket.onclose = (event) => {
    // A STALE socket's close must not clobber the live one.
    //
    // `_disconnect()` calls `_ws.close()`, which resolves asynchronously. If a
    // reconnect has already installed a new socket by the time this fires, the
    // unguarded version set `_ws = null` on top of it — so the tracked reference
    // was empty while an orphaned socket held the only live connection, and the
    // next `_connect()` opened a third. Prices kept arriving on whichever socket
    // happened to survive, which is why this shows up as flaky staleness rather
    // than as a clean disconnect.
    if (_ws !== socket) return;
    _ws = null;
    _lastFrameAt = 0;
    useStreamHealthStore.getState().setTransport(_refCount > 0 ? 'closed' : 'idle');
    _bump();
    if (event?.code === 4401) {
      // An expired access token can refresh; a revoked/deleted user must sign in.
      void refreshSterlingAccess().then(refreshed => {
        if (!refreshed && !getRefreshToken()) { notifyAuthExpired(); return; }
        _scheduleReconnect();
      });
      return;
    }
    _scheduleReconnect();
  };
  socket.onerror = () => socket.close();
}

function _disconnect() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_reconcileRetryTimer) {
    clearTimeout(_reconcileRetryTimer);
    _reconcileRetryTimer = null;
  }
  if (_ws) {
    _ws.close();
    _ws = null;
  }
  _lastFrameAt = 0;
  _attempt = 0;
  useStreamHealthStore.getState().setTransport('idle');
}

// ─── timers: watchdog, peer announcements, periodic re-assert ────────────────
function _watchdogTick() {
  const now = Date.now();
  if (
    _ws && _ws.readyState === WebSocket.OPEN
    && _lastActivityAt > 0 && now - _lastActivityAt > WATCHDOG_STALE_MS
  ) {
    // Open, yet not even a server heartbeat for 30s: a half-open connection the
    // browser has not noticed. Replace it instead of trusting it.
    _forceReconnect();
    return;
  }
  const trusted = tickFeedTrusted(now);
  if (trusted !== _lastTrusted) {
    _lastTrusted = trusted;
    _bump();   // consumers must re-merge: ticks stopped (or started) being trusted
  }
}

function _startTimers() {
  if (_timers.length) return;
  _timers = [
    setInterval(_watchdogTick, WATCHDOG_INTERVAL_MS),
    setInterval(_announceWants, PEER_ANNOUNCE_MS),
    setInterval(() => {
      if (_ws && _ws.readyState === WebSocket.OPEN && _desired.size) {
        _subscribed.clear();
        _scheduleReconcile();
      }
    }, RESUBSCRIBE_ALL_MS),
  ];
}

function _stopTimers() {
  _timers.forEach((t) => clearInterval(t));
  _timers = [];
}

/** Reconnect right now if the socket is gone or demonstrably dead. */
function _nudge() {
  if (_refCount <= 0) return;
  if (!_ws) {
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = null;
    }
    _attempt = 0;
    _connect();
    return;
  }
  // A backgrounded tab's timers are throttled, so the watchdog may not have run.
  if (_ws.readyState === WebSocket.OPEN && _lastActivityAt > 0
    && Date.now() - _lastActivityAt > WATCHDOG_STALE_MS / 2) {
    _forceReconnect();
  }
}

let _lifecycleListenersInstalled = false;
function installLifecycleListeners() {
  if (_lifecycleListenersInstalled || typeof window === 'undefined') return;
  _lifecycleListenersInstalled = true;
  window.addEventListener('online', _nudge);
  window.addEventListener('pageshow', _nudge);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') _nudge();
  });
  window.addEventListener('pagehide', () => postToPeers({ t: 'bye', from: TAB_ID }));
  onPeerMessage((m) => {
    if (m.t === 'want') _peers.update(m.from, m.tokens);
    else if (m.t === 'bye') _peers.remove(m.from);
    else if (m.t === 'hello' && _refCount > 0) _announceWants();
  });
}

installLifecycleListeners();

function _refConnect() {
  _refCount += 1;
  if (_refCount === 1) {
    _startTimers();
    postToPeers({ t: 'hello', from: TAB_ID });
  }
  _connect();
}

function _refDisconnect() {
  _refCount = Math.max(0, _refCount - 1);
  if (_refCount === 0) {
    _stopTimers();
    _disconnect();
    postToPeers({ t: 'want', from: TAB_ID, tokens: [] });
  }
}

/**
 * Forget everything learned for the previous account/session.
 *
 * Called on account switch, Kite logout and session change. Held ticks belong to
 * the old stream, and the backend tears its ticker down on those events, so the
 * subscription set must be asserted again against the new one.
 */
export function resetLiveTickState(): void {
  _tickByToken.clear();
  _tickReceivedAt.clear();
  _tokenVersions.clear();
  _lastFrameAt = 0;
  _subscribed.clear();
  _lastRebuildAt = 0;
  _bump();
  if (_refCount > 0) _scheduleReconcile();
}

// LIVE and SIMULATION are different tenants on the server, so a switch must not keep painting the
// other view's stream: forget the ticks and open a fresh socket for the view now selected.
useViewStore.subscribe((state, previous) => {
  if (state.view === previous.view) return;
  resetLiveTickState();
  // What the other view's socket said about the broker feed is not this view's to show.
  useStreamHealthStore.getState().setBroker(null);
  if (_ws) _forceReconnect();
});

function _subscribeStore(callback: () => void): () => void {
  _storeListeners.add(callback);
  return () => { _storeListeners.delete(callback); };
}

function _getVersion(): number {
  return _version;
}

/** Re-render callers on the coalesced cadence, after active interaction settles. */
export function useTickVersion(): number {
  return useSyncExternalStore(_subscribeStore, _getVersion, _getVersion);
}

/**
 * Reactive revision for only the tokens actually shown by a component.
 * A quote update on token 999 must not repaint a portfolio displaying only
 * token 111. Global stream generation still invalidates all on disconnect,
 * logout or LIVE/SIMULATION switch, so stale ticks cannot survive a reset.
 */
export function tokenDisplayRevision(tokensKey: string): string {
  const tokens = tokensKey ? tokensKey.split(',').map(Number) : [];
  return `${_feedEpoch}:${tokens.map(token => _tokenVersions.get(token) ?? 0).join(',')}`;
}

export function useTokenVersion(tokensKey: string): string {
  const snapshot = useCallback(() => tokenDisplayRevision(tokensKey), [tokensKey]);
  return useSyncExternalStore(_subscribeStore, snapshot, snapshot);
}

/** How long since a tick frame arrived, or null when none has yet. */
export function tickFeedAgeMs(nowMs: number = Date.now()): number | null {
  return _lastFrameAt ? nowMs - _lastFrameAt : null;
}

/** When ANY server frame (tick, heartbeat, status) last arrived; 0 when never. */
export function streamLastActivityAt(): number {
  return _ws ? _lastActivityAt : 0;
}

/** Whether the tick socket is open. Open is not the same as delivering. */
export function tickSocketOpen(): boolean {
  return _ws != null && _ws.readyState === WebSocket.OPEN;
}

/** Test hook: forget the recorded frame time. */
export function __resetTickFeedHealth(): void {
  _lastFrameAt = 0;
}

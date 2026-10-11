import { create } from 'zustand';

/**
 * Health of the browser <-> backend live stream, as the UI should report it.
 *
 * The socket module (`useKiteLiveTicks`) writes transitions here; the badge reads
 * them. High-frequency facts (the time of the last frame) are NOT stored -- they
 * would re-render every subscriber on every tick -- so callers pass them to
 * `deriveFeedHealth` alongside the store snapshot.
 */
export type Transport = 'idle' | 'connecting' | 'open' | 'closed';
export type FeedLevel = 'idle' | 'connecting' | 'live' | 'stale' | 'down' | 'broker-down';

/** Last `kite_stream_status` frame from the backend's Kite ticker. */
export interface BrokerFeedStatus {
  /** The backend has a ticker for this user (false: none started yet / stopped). */
  started: boolean;
  /** The ticker's Kite socket is connected right now. */
  connected: boolean;
  lastTickMs: number;
  lastError: string | null;
  reportedAt: number;
}

/** Server heartbeats arrive every ~10s when idle. Two missed = stale. */
export const STALE_AFTER_MS = 22_000;
/** A fresh connect gets this long before it counts as "down". */
export const CONNECTING_GRACE_MS = 5_000;

interface StreamHealthState {
  transport: Transport;
  transportSince: number;
  /** Continuous interruption start, preserved across retries. */
  interruptionSince: number;
  broker: BrokerFeedStatus | null;
  /** Backend process id from the last heartbeat; a change means it restarted. */
  bootId: string | null;
  reconnects: number;
  everClosed: boolean;
  setTransport: (transport: Transport) => void;
  setBroker: (broker: BrokerFeedStatus | null) => void;
  setBootId: (bootId: string | null) => void;
  reset: () => void;
}

const INITIAL = {
  transport: 'idle' as Transport,
  transportSince: 0,
  interruptionSince: 0,
  broker: null as BrokerFeedStatus | null,
  bootId: null as string | null,
  reconnects: 0,
  everClosed: false,
};

export const useStreamHealthStore = create<StreamHealthState>((set, get) => ({
  ...INITIAL,
  setTransport: (transport) => {
    const prev = get();
    if (prev.transport === transport) return;
    // Count a socket coming back (open after having been closed), not the first open.
    const reopened = transport === 'open' && prev.everClosed;
    set({
      transport,
      transportSince: Date.now(),
      interruptionSince: transport === 'open' || transport === 'idle'
        ? 0 : (prev.interruptionSince || Date.now()),
      reconnects: reopened ? prev.reconnects + 1 : prev.reconnects,
      everClosed: prev.everClosed || transport === 'closed',
    });
  },
  setBroker: (broker) => set({ broker }),
  setBootId: (bootId) => set({ bootId }),
  reset: () => set({ ...INITIAL }),
}));

export interface FeedHealth {
  level: FeedLevel;
  /** Short chip text. */
  label: string;
  /** One sentence for the tooltip / expanded line. */
  detail: string;
}

export interface FeedHealthInput {
  transport: Transport;
  transportSince: number;
  interruptionSince?: number;
  /** When ANY server frame (tick, heartbeat, status) last arrived; 0 = never. */
  lastActivityAt: number;
  broker: BrokerFeedStatus | null;
  now: number;
}

/**
 * What the operator should be told, from the facts. Pure so it can be tested
 * without a socket.
 *
 * The distinction that matters: "no ticks" is NOT "no feed". A quiet or closed
 * market delivers no ticks but the server still heartbeats, so socket silence
 * (stale) and a down broker feed are reported separately from a merely quiet
 * market, which is reported as live.
 */
export function deriveFeedHealth(i: FeedHealthInput): FeedHealth {
  if (i.transport === 'idle') {
    return { level: 'idle', label: '', detail: '' };
  }
  const interruptionAge = i.now - (i.interruptionSince || i.transportSince);
  if (i.transport === 'closed') {
    if (interruptionAge <= CONNECTING_GRACE_MS) {
      return { level: 'connecting', label: 'RECONNECTING…', detail: 'Restoring the live stream; prices use the last fetched data until connected.' };
    }
    return {
      level: 'down',
      label: 'LIVE FEED OFFLINE',
      detail: 'Disconnected from the Sterling backend. Reconnecting; screens show the last data fetched, not live data.',
    };
  }
  if (i.transport === 'connecting') {
    const waited = interruptionAge;
    return waited > CONNECTING_GRACE_MS
      ? {
        level: 'down',
        label: 'LIVE FEED OFFLINE',
        detail: 'Cannot reach the Sterling backend. Retrying; screens show the last data fetched, not live data.',
      }
      : { level: 'connecting', label: 'CONNECTING…', detail: 'Opening the live stream.' };
  }
  // open
  const silentFor = i.lastActivityAt > 0 ? i.now - i.lastActivityAt : i.now - i.transportSince;
  if (silentFor > STALE_AFTER_MS) {
    const secs = Math.round(silentFor / 1000);
    return {
      level: 'stale',
      label: `STALE ${secs}s`,
      detail: `The live stream has been silent for ${secs}s. Data on screen may be out of date.`,
    };
  }
  if (i.broker && i.broker.started && !i.broker.connected) {
    return {
      level: 'broker-down',
      label: 'BROKER FEED DOWN',
      detail: i.broker.lastError
        ? `The backend lost its Kite market-data socket (${i.broker.lastError}). Prices fall back to slow polling.`
        : 'The backend lost its Kite market-data socket. Prices fall back to slow polling.',
    };
  }
  if (i.lastActivityAt === 0) {
    return { level: 'connecting', label: 'CONNECTING…', detail: 'Waiting for the first frame.' };
  }
  return { level: 'live', label: 'LIVE', detail: 'Live stream healthy.' };
}

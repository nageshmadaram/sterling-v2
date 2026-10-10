import { create } from 'zustand';
import type { PlaceGttBody } from '../types/kite';

export interface PendingProtection {
  orderId: string;
  gtt: PlaceGttBody;
}

interface PendingProtectionState {
  pending: PendingProtection[];
  add: (entry: PendingProtection) => void;
  remove: (orderId: string) => void;
  /** Drop everything (account switch: these belong to the previous account). */
  clear: () => void;
}

/** Bounds: a trading day does not produce more, and a runaway must not grow forever. */
export const MAX_PENDING_PROTECTIONS = 25;
/** An entry older than a trading day is for an order that no longer matters. */
export const PENDING_PROTECTION_TTL_MS = 20 * 60 * 60 * 1000;
const STORAGE_KEY = 'sterling.kite.pending-protection.v1';

interface StoredEntry extends PendingProtection { savedAt: number }

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

function readStored(): StoredEntry[] | null {
  const s = storage();
  if (!s) return null;
  try {
    const raw = JSON.parse(s.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(raw)) return [];
    const now = Date.now();
    return raw.filter((e): e is StoredEntry =>
      !!e && typeof e.orderId === 'string' && !!e.gtt && typeof e.savedAt === 'number'
      && now - e.savedAt < PENDING_PROTECTION_TTL_MS);
  } catch {
    return [];
  }
}

function writeStored(list: StoredEntry[]): void {
  try { storage()?.setItem(STORAGE_KEY, JSON.stringify(list)); } catch { /* quota/private mode */ }
}

const strip = (list: StoredEntry[]): PendingProtection[] =>
  list.map(({ orderId, gtt }) => ({ orderId, gtt }));

/**
 * Orders placed with a Stoploss/Target toggle in OrderWindow need their
 * protective GTT created once the order actually FILLS, not the instant
 * it's accepted — OrderWindow closes immediately on submit, so this state
 * has to outlive the ticket. See PendingGttProtectionWatcher (mounted once
 * near the app root) for the consumer side.
 *
 * It also has to outlive a page reload: held only in memory, a refresh between
 * "order accepted" and "order filled" silently dropped the protection for a live
 * position. The list is therefore mirrored to localStorage and kept in sync
 * across tabs. Because every open tab then sees the same entry, FIRING it must be
 * claimed exactly once -- see `claimPendingProtection`.
 */
export const useKitePendingProtectionStore = create<PendingProtectionState>((set, get) => ({
  pending: strip(readStored() ?? []),
  add: (entry) => {
    const rest = get().pending.filter((p) => p.orderId !== entry.orderId);
    const next = [...rest, entry].slice(-MAX_PENDING_PROTECTIONS);
    set({ pending: next });
    const now = Date.now();
    writeStored(next.map((p) => ({ ...p, savedAt: now })));
  },
  remove: (orderId) => {
    const next = get().pending.filter((p) => p.orderId !== orderId);
    set({ pending: next });
    const stored = readStored();
    if (stored) writeStored(stored.filter((p) => p.orderId !== orderId));
  },
  clear: () => {
    set({ pending: [] });
    writeStored([]);
  },
}));

type LockManagerLike = {
  request: <T>(
    name: string,
    options: { ifAvailable: boolean },
    callback: (lock: unknown | null) => Promise<T> | T,
  ) => Promise<T>;
};

/**
 * Take ownership of firing a pending protection. Resolves with the entry to fire,
 * or null when another tab (or an earlier call) already owns it.
 *
 * Removing the entry from shared storage IS the claim, and the read-then-remove
 * runs under a Web Lock so two tabs cannot both see it: two protective GTTs
 * against one position would over-sell it.
 */
export async function claimPendingProtection(orderId: string): Promise<PendingProtection | null> {
  const take = (): PendingProtection | null => {
    const stored = readStored();
    if (stored === null) {
      // No shared storage to arbitrate through: fall back to this tab's memory.
      const hit = useKitePendingProtectionStore.getState().pending.find((p) => p.orderId === orderId);
      if (hit) useKitePendingProtectionStore.getState().remove(orderId);
      return hit ?? null;
    }
    const hit = stored.find((p) => p.orderId === orderId);
    const rest = stored.filter((p) => p.orderId !== orderId);
    if (hit) writeStored(rest);
    useKitePendingProtectionStore.setState({ pending: strip(rest) });
    return hit ? { orderId: hit.orderId, gtt: hit.gtt } : null;
  };

  const locks = typeof navigator !== 'undefined'
    ? (navigator as unknown as { locks?: LockManagerLike }).locks
    : undefined;
  if (locks?.request) {
    try {
      return await locks.request(
        `sterling-gtt-claim:${orderId}`, { ifAvailable: true },
        async (lock) => (lock ? take() : null),
      );
    } catch {
      /* Web Locks refused (insecure context, etc.): fall through to the storage claim */
    }
  }
  return take();
}

// Keep tabs consistent: another tab added, removed or claimed an entry.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return;
    const stored = readStored();
    if (stored) useKitePendingProtectionStore.setState({ pending: strip(stored) });
  });
}

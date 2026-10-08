import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';

const OPS = '/api/v1/ops';

/**
 * Live safety status, normalised.
 *
 * Two shapes are read, because both exist:
 *   - the compact contract
 *       { safe_mode: { state, reason, triggers }, live_execution: boolean,
 *         recovery_state: string, exposure_observation: { fresh, age_s } }
 *   - the backend's GET /ops/live-status
 *       { safe_mode: { state, active, reason, triggers }, live_execution: { enabled, kill_switch },
 *         recovery: { overall }, exposure: { observed, fresh, age_seconds } }
 * Every field may be absent or the wrong type, and the endpoint may 404 on an older
 * backend. Anything unreadable stays `null` ("unknown"), never a comforting default.
 */
export interface LiveStatus {
  safeMode: {
    state: string | null;
    reason: string | null;
    triggers: string[];
    /** true when lifting needs a person to say they investigated (non-OPERATOR triggers). */
    requiresAck: boolean;
  };
  /** false = new-entry origination is off (kill switch or master floor). */
  liveExecution: boolean | null;
  /** true only when the operator kill switch itself is engaged. */
  killSwitch: boolean | null;
  recoveryState: string | null;
  exposure: { observed: boolean | null; fresh: boolean | null; ageS: number | null };
}

export type LiveStatusResult =
  | { available: true; status: LiveStatus }
  | { available: false; status: null };

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() !== '' ? v.trim() : null;
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function normalizeLiveStatus(raw: unknown): LiveStatus | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const sm = obj(r.safe_mode);
  const ex = obj(r.exposure_observation ?? r.exposure);
  const triggers = Array.isArray(sm.triggers)
    ? sm.triggers.filter((t): t is string => typeof t === 'string' && t !== '')
    : [];

  // live_execution: a boolean, or { enabled, kill_switch }.
  let liveExecution: boolean | null = null;
  let killSwitch: boolean | null = null;
  if (typeof r.live_execution === 'boolean') {
    liveExecution = r.live_execution;
  } else if (r.live_execution && typeof r.live_execution === 'object') {
    const le = obj(r.live_execution);
    if (typeof le.kill_switch === 'boolean') killSwitch = le.kill_switch;
    if (typeof le.enabled === 'boolean') liveExecution = le.enabled && le.kill_switch !== true;
    else if (killSwitch === true) liveExecution = false;
  }

  const recoveryState = str(r.recovery_state) ?? str(obj(r.recovery).overall);
  const observed = typeof ex.observed === 'boolean' ? ex.observed : null;

  return {
    safeMode: {
      // `active` is authoritative when the backend sends it; otherwise the state string decides.
      state: str(sm.state),
      reason: str(sm.reason),
      triggers,
      requiresAck: typeof sm.requires_acknowledgement === 'boolean'
        ? sm.requires_acknowledgement
        : triggers.some((t) => t.toUpperCase() !== 'OPERATOR'),
    },
    liveExecution,
    killSwitch,
    recoveryState,
    exposure: {
      observed,
      fresh: typeof ex.fresh === 'boolean' ? ex.fresh : null,
      ageS: num(ex.age_s) ?? num(ex.age_seconds),
    },
  };
}

/**
 * Safe mode blocks new entries unless the state is explicitly NORMAL. An unreadable
 * or missing state is an unknown, and the safe answer to an unknown is "blocked".
 * Returns null only when the status itself is unavailable (older backend).
 */
export function safeModeEngaged(status: LiveStatus | null): boolean | null {
  if (!status) return null;
  const state = status.safeMode.state;
  if (state === null) return null;
  return state.toUpperCase() !== 'NORMAL';
}

/** Recovery states that mean "nothing is waiting on an operator". */
const RECOVERY_CLEAR = new Set(['', 'NORMAL', 'CLEAR', 'OK', 'NONE', 'IDLE', 'HEALTHY', 'CLEAN']);

export function recoveryNeedsAttention(status: LiveStatus | null): boolean {
  const state = status?.recoveryState;
  if (!state) return false;
  return !RECOVERY_CLEAR.has(state.toUpperCase());
}

const RECOVERY_TEXT: Record<string, string> = {
  RECOVERY_REQUIRED: 'recovery required',
  PENDING: 'recovery pending',
  NO_LIVE_ACCOUNT: 'no live account bound',
  ERROR: 'recovery error',
};

/** Operator-facing words for a recovery state ("recovery required", not RECOVERY_REQUIRED). */
export function recoveryLabel(state: string | null | undefined): string {
  if (!state) return '';
  return RECOVERY_TEXT[state.toUpperCase()] ?? state.toLowerCase().replace(/_/g, ' ');
}

export const LIVE_STATUS_KEY = ['ops-live-status'] as const;

export function useLiveStatus() {
  return useQuery<LiveStatusResult>({
    queryKey: LIVE_STATUS_KEY,
    queryFn: async (): Promise<LiveStatusResult> => {
      try {
        const raw = await api.get<unknown>(`${OPS}/live-status`);
        const status = normalizeLiveStatus(raw);
        return status ? { available: true, status } : { available: false, status: null };
      } catch (err) {
        // 404/405: the endpoint does not exist on this backend. Degrade quietly.
        const code = (err as { status?: number } | null)?.status;
        if (code === 404 || code === 405) return { available: false, status: null };
        throw err;
      }
    },
    refetchInterval: (query) => (query.state.data?.available === false ? false : 15_000),
    staleTime: 5_000,
    retry: false,
  });
}

const RESUME_PHRASE = 'RESUME TRADING';

export interface ResumeSafeModeArgs {
  reason: string;
  /** The operator says they looked at the condition. Needed for non-OPERATOR triggers. */
  acknowledgeInvestigation?: boolean;
}

export function useResumeSafeMode() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, ResumeSafeModeArgs>({
    mutationFn: async ({ reason, acknowledgeInvestigation }) => {
      // The backend names the phrase it wants; fall back to the documented one.
      let phrase = RESUME_PHRASE;
      try {
        const detail = obj(await api.get<unknown>(`${OPS}/safe-mode`));
        phrase = str(detail.confirmation_phrase) ?? RESUME_PHRASE;
      } catch {
        /* keep the default */
      }
      return api.post(`${OPS}/safe-mode/resume`, {
        confirm: phrase,
        reason,
        acknowledge_investigation: acknowledgeInvestigation === true,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LIVE_STATUS_KEY });
      qc.invalidateQueries({ queryKey: ['kite-engine-readiness'] });
      notifyOrder({ kind: 'info', title: 'Trading resumed', message: 'Safe mode cleared. New entries are allowed again.' });
    },
    onError: (err) => {
      notifyOrder({ kind: 'error', title: 'Resume refused', message: err.message || 'Safe mode could not be cleared.' });
    },
  });
}

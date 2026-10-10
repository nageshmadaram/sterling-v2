/**
 * The Simulation control plane (`/api/v1/sim/*`): the virtual account's status, the per-strategy
 * MANUAL | SIMULATION | AUTO mode, reset and virtual capital.
 *
 * These routes belong to the owner and answer from either view, so they never depend on which
 * tenant is on screen. Everything here is virtual money; nothing places a real order.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useView } from '../store/useViewStore';

export const SIM = '/api/v1/sim';
export const SIM_STATUS_KEY = ['sim-status'] as const;

export type StrategyKey = 'supertrend' | 'snapback' | 'gamma';
export type StrategyMode = 'MANUAL' | 'SIMULATION' | 'AUTO';

export const STRATEGY_MODES: readonly StrategyMode[] = ['MANUAL', 'SIMULATION', 'AUTO'];

/** The three strategies Sterling runs, in the order the app lists them. */
export const SIM_STRATEGIES: ReadonlyArray<{ key: StrategyKey; label: string; blurb: string }> = [
  { key: 'supertrend', label: 'Supertrend', blurb: 'Follows the market trend.' },
  { key: 'snapback', label: 'Snapback', blurb: 'Looks for a quick bounce back after a sharp move.' },
  { key: 'gamma', label: 'Gamma Unwind', blurb: 'Trades the swing as option positions unwind near expiry.' },
];

export function strategyLabel(key: string): string {
  return SIM_STRATEGIES.find((s) => s.key === key)?.label ?? key;
}

export interface TenantArm {
  master: boolean;
  strategy: boolean;
  armed: boolean;
  enabled: boolean;
}

export interface SimStrategyState {
  strategy: StrategyKey;
  mode: StrategyMode;
  /** Both the real and the virtual side are armed (the label then reads AUTO). */
  conflict: boolean;
  live: TenantArm;
  simulation: TenantArm & { configured: boolean };
}

export type SimStrategies = Partial<Record<StrategyKey, SimStrategyState>>;

export interface SimStatus {
  virtual: boolean;
  capital: number;
  cash: number;
  equity: number;
  available?: number;
  unrealised?: number;
  realised_today?: number;
  net_realised_today?: number;
  today_pnl: number;
  return_pct?: number;
  open_positions: number;
  open_orders?: number;
  active_gtts?: number;
  trades_today?: number;
  message?: string;
  runtime: {
    running: boolean;
    /** Epoch ms of the last price pass; 0 before the first one. */
    last_pass_ms: number;
    passes?: number;
    last_error?: string;
    session_open: boolean;
  };
  price_feed: { available: boolean; detail: string };
  strategies: SimStrategies;
}

export interface ModeResult {
  strategy: StrategyKey;
  mode: StrategyMode;
  changed?: boolean;
  previous?: StrategyMode;
  strategies?: SimStrategies;
}

export interface ResetResult {
  ok: boolean;
  cleared?: string[];
  failed?: Record<string, string>;
  capital?: number;
  cash?: number;
  strategies?: SimStrategies;
  status?: SimStatus;
}

// ─── errors ──────────────────────────────────────────────────────────────────

export interface SimErrorView {
  status: number | null;
  /** One or two plain sentences, safe to show to anyone. */
  text: string;
  /** Readiness blockers (409), shown as a list. */
  reasons: string[];
}

type Detail = { code?: unknown; error?: unknown; message?: unknown; reasons?: unknown };

/** The API layer flattens an object `detail` to JSON text; read the pieces back out. */
function detailOf(err: unknown): { status: number | null; detail: Detail; raw: string } {
  const e = (err ?? {}) as { status?: unknown; message?: unknown };
  const status = typeof e.status === 'number' ? e.status : null;
  const raw = typeof e.message === 'string' ? e.message : '';
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { status, detail: parsed as Detail, raw };
    }
  } catch { /* a plain-text detail */ }
  return { status, detail: {}, raw };
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

/**
 * Short, plain messages for what the backend can answer. The backend's own wording is kept only
 * where it is already plain (readiness reasons); the rest is rewritten for someone who has never
 * seen a status code.
 */
export function describeSimError(err: unknown): SimErrorView {
  const { status, detail, raw } = detailOf(err);
  const code = str(detail.code) || str(detail.error);
  const message = str(detail.message) || (raw.startsWith('{') ? '' : raw);
  const reasons = Array.isArray(detail.reasons)
    ? detail.reasons.filter((r): r is string => typeof r === 'string' && r.trim() !== '')
    : [];

  if (status === null) {
    return { status, reasons: [], text: 'Could not reach Sterling. Check your connection and try again.' };
  }
  if (status === 409) {
    return {
      status, reasons,
      text: reasons.length > 0
        ? 'Not switched. Fix these first, then try again:'
        : 'Not switched. The readiness check found something to fix first.',
    };
  }
  if (status === 503) {
    return code === 'owner_misconfigured'
      ? { status, reasons: [], text: 'Simulation is not set up correctly on the server. The owner needs to check its settings.' }
      : { status, reasons: [], text: 'Simulation is not available right now. The virtual account could not be prepared. Try again in a moment.' };
  }
  if (status === 500) {
    return {
      status, reasons: [],
      text: 'The switch failed part-way. The strategy was left OFF (MANUAL), so it is not armed in two places. Check it and try again.',
    };
  }
  if (status === 403) {
    return code === 'owner_only'
      ? { status, reasons: [], text: 'Simulation is for the owner of this account only.' }
      : { status, reasons: [], text: 'Not available in the Simulation view. Switch to LIVE to do this.' };
  }
  if (status === 422 || status === 400) {
    return { status, reasons: [], text: message || 'That value was not accepted.' };
  }
  return { status, reasons: [], text: message || `Something went wrong (error ${status}).` };
}

/**
 * GET /sim/status answers 403 to anyone but the owner (a non-admin, or a remembered Simulation view
 * that a non-owner inherited). The whole Simulation feature then stays out of sight.
 */
export function isOwnerDenied(err: unknown): boolean {
  return detailOf(err).status === 403;
}

// ─── run state ───────────────────────────────────────────────────────────────

export type RunTone = 'good' | 'warn' | 'bad' | 'quiet';
export interface SimRunState {
  key: 'running' | 'stalled' | 'paused' | 'closed' | 'stopped' | 'starting';
  tone: RunTone;
  label: string;
  detail: string;
}

/** The loop runs every couple of seconds; this long without a pass means it is stuck. */
export const STALL_AFTER_MS = 30_000;

export function simRunState(status: SimStatus | null | undefined, now: number): SimRunState {
  const rt = status?.runtime;
  if (!status || !rt) {
    return { key: 'starting', tone: 'quiet', label: 'Checking…', detail: 'Waiting for the simulation to report.' };
  }
  if (!rt.running) {
    return {
      key: 'stopped', tone: 'bad', label: 'Stopped',
      detail: 'The simulation is not running, so no virtual trades are being managed.',
    };
  }
  if (rt.session_open === false) {
    return {
      key: 'closed', tone: 'quiet', label: 'Market closed',
      detail: 'The simulation checks prices again when the market opens.',
    };
  }
  if (status.price_feed && status.price_feed.available === false) {
    return {
      key: 'paused', tone: 'warn', label: 'Paused',
      detail: status.price_feed.detail || 'There are no live prices, so the simulation is waiting.',
    };
  }
  if (!rt.last_pass_ms) {
    return { key: 'starting', tone: 'quiet', label: 'Starting', detail: 'Waiting for the first price check.' };
  }
  const age = now - rt.last_pass_ms;
  if (age > STALL_AFTER_MS) {
    const secs = Math.max(1, Math.round(age / 1000));
    const why = rt.last_error ? ` Last problem: ${rt.last_error}` : '';
    return {
      key: 'stalled', tone: 'warn', label: 'Stalled',
      detail: `No price check for ${secs >= 120 ? `${Math.round(secs / 60)} minutes` : `${secs} seconds`}.${why}`,
    };
  }
  return { key: 'running', tone: 'good', label: 'Running', detail: 'Prices are being checked every few seconds.' };
}

/** A clock that re-renders its caller; the run indicator must go stale on its own. */
export function useNow(intervalMs = 5_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

// ─── queries and mutations ──────────────────────────────────────────────────

export function useSimStatus(enabled = true) {
  const view = useView();
  return useQuery<SimStatus>({
    queryKey: SIM_STATUS_KEY,
    queryFn: () => api.get<SimStatus>(`${SIM}/status`),
    enabled,
    // The Simulation view is watched closely; the live view only needs to notice a change of mode.
    refetchInterval: view === 'simulation' ? 5_000 : 15_000,
    staleTime: 3_000,
  });
}

/** The mode of one strategy, or MANUAL when the status has not loaded or does not know it. */
export function modeOf(status: SimStatus | undefined, key: StrategyKey): StrategyMode {
  return status?.strategies?.[key]?.mode ?? 'MANUAL';
}

/** Strategies currently in SIMULATION mode, in display order. */
export function simulatedStrategies(status: SimStatus | undefined): StrategyKey[] {
  return SIM_STRATEGIES.map((s) => s.key).filter((k) => status?.strategies?.[k]?.mode === 'SIMULATION');
}

export function useSetStrategyMode() {
  const qc = useQueryClient();
  return useMutation<ModeResult, Error, { strategy: StrategyKey; mode: StrategyMode }>({
    mutationFn: ({ strategy, mode }) => api.put<ModeResult>(`${SIM}/strategy/${strategy}/mode`, { mode }),
    onSuccess: (res) => {
      qc.setQueryData<SimStatus>(SIM_STATUS_KEY, (old) => (
        old && res?.strategies ? { ...old, strategies: res.strategies } : old
      ));
      void qc.invalidateQueries({ queryKey: SIM_STATUS_KEY });
    },
  });
}

export function useSetSimCapital() {
  const qc = useQueryClient();
  return useMutation<SimStatus, Error, number>({
    mutationFn: (capital) => api.put<SimStatus>(`${SIM}/capital`, { capital }),
    onSuccess: (status) => { qc.setQueryData(SIM_STATUS_KEY, status); },
  });
}

export function useResetSimulation() {
  const qc = useQueryClient();
  return useMutation<ResetResult, Error, void>({
    // Only a JSON `true` confirms on the server; the typed confirmation is the UI's half.
    mutationFn: () => api.post<ResetResult>(`${SIM}/reset`, { confirm: true }),
    onSuccess: (res) => {
      if (res?.status) qc.setQueryData(SIM_STATUS_KEY, res.status);
      // Positions, orders, trades and the journal were wiped: every screen re-reads its book.
      void qc.invalidateQueries();
    },
  });
}

import { useSimNowMs, useReplayStore } from './useReplayStore';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';

const KEY = ['gamma-move-config'];
const SNAPSHOT_KEY = ['gamma-move-snapshot'];
const HISTORY_KEY = ['gamma-move-history'];
const BASE = '/api/v1/config/gamma-move';

export type StopMode = 'monitor' | 'broker' | 'both';
export type DirectionMode = 'any' | 'all';
export type GammaTier = 'ENTRY' | 'STRONG_WATCHLIST' | 'WATCHLIST' | 'NEAR ENTRY' | 'WATCH ONLY';
export type SignalState = 'watching' | 'armed';

export interface GammaMoveConfig {
  enabled: boolean;
  auto_execute: boolean;
  scan_stocks: string[];
  scan_all_stocks: boolean;
  stock_contracts: boolean;
  execution_lots: number;
  stop_mode: StopMode;

  zone_pivot_len: number;
  zone_atr_len: number;
  zone_depth: number;
  zone_guard_mult: number;
  zone_memory: number;
  zone_break_on_wick: boolean;
  zone_daily_bars: number;

  stock_band_pct: number;
  strike_band_pct: number;
  top_n_oi: number;
  min_premium_ticks: number;
  min_oi_lots: number;
  min_prev_turnover_rs: number;

  expiry_dte_threshold: number;
  mark_levels_times: string[];
  build_watchlists_times: string[];
  token_poll_minutes: number;

  base_tf_min: number;
  signal_tf_min: number;
  display_tf_min: number;
  oi_drop_alert: number;
  oi_drop_tolerance: number;
  oi_drop_strong: number;
  oi_rearm_extra: number;
  vol_sma_len: number;
  vol_build_min_of_last3: number;
  consol_bars: number;
  consol_atr_len: number;
  consol_range_atr: number;
  consol_drift_range_atr: number;
  h_min_range_ticks: number;
  h_max_zero_vol_slots: number;

  alert_group_rate_per_min: number;
  alert_per_chat_rate_per_sec: number;
  late_day_warn_ist: string;
  expiry_warn_dte: number;

  swing_days: number;

  gates_enabled: boolean;
  gates_all_lists: boolean;
  zone_max_gap_pct: number;
  liq_sma_bars: number;
  liq_min_avg_lots: number;
  liq_max_zero_vol_bars: number;
  direction_enabled: boolean;
  unwind_enabled: boolean;
  unwind_min_ticks: number;
  v1_direction_mode: DirectionMode;
  v1_zone_atr_k: number;
  v1_zone_max_pct: number;
}

export interface GammaMoveStrategyInfo {
  id: string;
  name: string;
  contract_version: string;
  reference_package: string;
  spec_version: string;
  edition: string;
  tagline: string;
  how_it_works: string;
  provenance: string;
  signal_parity: boolean;
  client_top_n_oi: number;
  execution_note: string;
  enabled: boolean;
}

export interface GammaMoveResponse {
  strategy: GammaMoveStrategyInfo;
  config: GammaMoveConfig;
  defaults: GammaMoveConfig;
  vocabularies: {
    stop_mode?: StopMode[];
    v1_direction_mode?: DirectionMode[];
    scan_stocks?: string[];
  };
  client_effective: {
    top_n_oi: number;
    base_tf_min: number;
    signal_tf_min: number;
    display_tf_min: number;
  };
  warnings: string[];
}

export interface GammaGateContext {
  zone_gap_pct: number | null;
  zone_limit_pct: number;
  avg_vol_lots: number | null;
  zero_vol_bars: number | null;
  f1: boolean | null;
  f2: boolean | null;
  unwind: boolean | null;
  gate_reasons: string[];
  gate_unknown: string[];
  passes: string[];
  tier: GammaTier;
  quadrant: string | null;
}

export interface GammaMetrics {
  oi_drop_pct?: number | null;
  vol_ratio?: number | null;
  base_low?: number | null;
  base_high?: number | null;
  a?: boolean;
  b?: boolean;
  c?: boolean;
  h?: boolean;
  strong?: boolean;
  b_count?: number;
  sma?: number | null;
  range?: number | null;
  drift?: number | null;
  zero_vol_slots?: number | null;
}

/** Server verdict on whether an ENTRY row can be taken right now, and why not. */
export interface GammaEntryGate {
  eligible: boolean;
  /** Machine code, empty when eligible. */
  code: string;
  /** Operator-facing reason, empty when eligible. */
  reason: string;
  hold: 'intraday' | 'swing';
}

export interface GammaSignalRow {
  id: string;
  source?: 'live_recorded' | 'historical_replay' | 'live';
  state: SignalState;
  tier: GammaTier;
  at_ms: number;
  underlying: string;
  reason: string;
  warnings?: string[];
  list_name: string;
  tag: string;
  strong: boolean;
  instrument: {
    instrument_id: string;
    tradingsymbol: string;
    option_type: 'CE' | 'PE';
    strike: number;
    expiry: string;
    lot_size: number;
    tick_size: number;
    exchange: string;
  };
  watch: {
    underlying_token: number;
    ref_price: number;
    zone_edge: number;
    atr_pct: number | null;
    oi: number;
    candidates_n: number;
    dte: number | null;
    build_oi: number | null;
    chain_ltp: number | null;
    carried?: boolean;
  };
  metrics: GammaMetrics | null;
  gates: GammaGateContext | null;
  levels: {
    /** Latest tick of the contract; null when it is not being watched live. */
    ltp: number | null;
    /** Seconds since that tick (or bar close). Absent on archived rows. */
    ltp_age_s?: number | null;
    entry: number | null;
    stop: number | null;
    t1: number | null;
    t2: number | null;
  };
  /** Present on today's live E1 rows; absent on history and watch-only rows. */
  entry_gate?: GammaEntryGate;
  snapshot?: Record<string, unknown>;
}

export interface GammaPositionRow {
  symbol: string;
  signal_id: string;
  underlying: string;
  entry: number;
  effective_entry: number;
  ltp?: number | null;
  ltp_age_s?: number | null;
  /** Cash already booked by partial exits of this position. */
  partial_realised_inr?: number;
  exiting?: boolean;
  pnl_reconciliation_required?: boolean;
  quantity: number;
  lots: number;
  entered_ms: number;
  entry_day: string;
  hold: 'intraday' | 'swing';
  trade_state: 'OPEN' | 'T1_TRAIL' | 'CLOSED';
  zone_edge: number;
  base_low: number;
  t1: number;
  t2: number;
  stop: number | null;
  sessions_held: number;
  swing_armed: boolean;
  status: 'pending' | 'open' | 'closed' | 'rejected';
  order_id: string;
  gtt_id: number;
  gtt_at: number;
  gtt_qty: number;
  broker_stop: boolean;
  stop_mode: StopMode;
}

export interface GammaScanState {
  last_run_ms?: number;
  issue?: string;
  replay_resolution?: string;
  stage_a?: { scanned: number; near_level: number | null; seconds: number | null };
  stage_b?: { candidates: number; seconds: number | null };
  stage_c?: {
    watched: number; armed: number; historical_requests: number; seconds: number | null;
  };
  total_seconds?: number;
  recovered_slot?: string;
  recovery_source?: string;
  coverage?: string[];
}

export interface GammaTradeRecord {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  consecutive_losses: number;
  consecutive_wins: number;
  realised_inr: number;
  day_realised_inr: number;
  day: string;
  verdict: string;
}

export interface GammaSessionStatus {
  day: string;
  phase: string;
  halt_reason: string;
  trades_today: number;
  signals: GammaSignalRow[];
  candidates: GammaSignalRow[];
  positions: GammaPositionRow[];
  record: GammaTradeRecord;
  subscribed: number[];
  notes: { kind: string; message: string; at_ms: number }[];
}

export interface GammaModeState {
  is_paper: boolean | null;
  auto_execute: boolean;
  live_execution_enabled: boolean;
  note: string;
}

export interface GammaHistoryDay {
  is_trading_day?: boolean;
  day: string;
  signals: number;
  watches: number;
  entry?: number;
  strong_watchlist?: number;
  watchlist_e1?: number;
  last_run_ms: number;
  updated_ms: number;
  issue: string;
  coverage?: string[];
  source?: 'live_recorded' | 'historical_replay' | 'available_replay' | 'live';
  resolution?: string;
}

export interface GammaHistoryLedger {
  days: GammaHistoryDay[];
  signals: Array<GammaSignalRow & {
    history_day?: string;
    history_resolution?: string;
    history_complete?: boolean;
  }>;
  count: number;
  tier_counts: {
    entry: number;
    strong_watchlist: number;
    watchlist_e1: number;
  };
}

export interface GammaMoveSnapshot {
  day: string;
  today: string;
  historical: boolean;
  recorded: boolean;
  source: 'live_recorded' | 'historical_replay' | 'available_replay' | 'live';
  replay: { resolution?: string; complete?: boolean };
  coverage: string[];
  replay_available: boolean;
  is_trading_day: boolean;
  latest_trading_day: string;
  history_days: GammaHistoryDay[];
  strategy: GammaMoveStrategyInfo;
  config: GammaMoveConfig;
  scan: GammaScanState;
  session: GammaSessionStatus | null;
  candidates: GammaSignalRow[];
  positions: GammaPositionRow[];
  record: GammaTradeRecord;
  mode: GammaModeState;
  warnings: string[];
  blockers: string[];
}

export function useGammaMoveConfig() {
  return useQuery<GammaMoveResponse>({
    queryKey: KEY,
    queryFn: () => api.get(BASE),
    staleTime: 30_000,
  });
}

export function useGammaMoveSnapshot(
  enabled = true,
  refetchInterval = 0,
  day?: string,
  todayOnly = false,
) {
  const replay = useSimNowMs() !== null;
  const session = useReplayStore(s => s.status.session_id);
  const historical = Boolean(day) && !replay;
  const priorityOnly = todayOnly && !replay && !historical;
  return useQuery<GammaMoveSnapshot>({
    // A lean first-day response must not overwrite the full history index.
    queryKey: [...SNAPSHOT_KEY, replay ? session || 'replay' : day || (priorityOnly ? 'today-priority' : 'today')],
    queryFn: () => api.get(
      day && !replay
        ? `${BASE}/snapshot?day=${encodeURIComponent(day)}`
        : `${BASE}/snapshot${priorityOnly ? '?today_only=true' : ''}`,
    ),
    enabled,
    staleTime: historical ? 30_000 : 2_000,
    refetchInterval: enabled && !historical
      ? (refetchInterval || 2_000)
      : false,
  });
}

export function useGammaMoveHistory(enabled = true, days = 60, limit = 500) {
  return useQuery<GammaHistoryLedger>({
    queryKey: [...HISTORY_KEY, days, limit],
    queryFn: () => api.get(
      `${BASE}/history?days=${encodeURIComponent(days)}&limit=${encodeURIComponent(limit)}`,
    ),
    enabled,
    staleTime: 10_000,
    refetchInterval: enabled ? 15_000 : false,
  });
}


export function useUpdateGammaMove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<GammaMoveConfig>) => api.put(BASE, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: HISTORY_KEY });
      notifyOrder({
        kind: 'info', title: 'Gamma Unwind updated',
        message: 'Gamma Unwind settings saved.',
      });
    },
    onError: (err: any) => notifyOrder({
      kind: 'error', title: 'Save failed',
      message: err?.message || 'Could not update Gamma Unwind settings.',
    }),
  });
}

export function useLoadGammaMoveHistoricalDay() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ day, force = false }: { day: string; force?: boolean }) =>
      api.post<GammaMoveSnapshot>(`${BASE}/history/replay`, { day, force }),
    onSuccess: (snapshot) => {
      qc.setQueryData([...SNAPSHOT_KEY, snapshot.day], snapshot);
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: HISTORY_KEY });
      notifyOrder({
        kind: 'info',
        title: 'Gamma day rebuilt',
        message: `${snapshot.day}: ${snapshot.candidates.length} watch/signal row(s) loaded.`,
      });
    },
    onError: (err: any) => notifyOrder({
      kind: 'error',
      title: 'Historical Gamma load failed',
      message: err?.message || 'Could not reconstruct the selected Gamma-Unwind day.',
    }),
  });
}


export function useBackfillGammaMoveHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (days: string[]) => {
      const unique = [...new Set(days.filter(Boolean))].slice(0, 5);
      const completed: string[] = [];
      const failed: Array<{ day: string; error: string }> = [];
      for (const day of unique) {
        try {
          await api.post<GammaMoveSnapshot>(`${BASE}/history/replay`, { day, force: false });
          completed.push(day);
        } catch (err: any) {
          failed.push({ day, error: err?.message || 'historical load failed' });
        }
      }
      return { completed, failed };
    },
    onSuccess: ({ completed, failed }) => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: HISTORY_KEY });
      notifyOrder({
        kind: 'info',
        title: 'Gamma history loaded',
        message: failed.length
          ? `Rebuilt ${completed.length}; ${failed.length} session(s) could not be reconstructed.`
          : `Rebuilt ${completed.length} recent Gamma session(s).`,
      });
    },
    onError: (err: any) => notifyOrder({
      kind: 'error',
      title: 'Gamma history load failed',
      message: err?.message || 'Could not load recent Gamma historical sessions.',
    }),
  });
}


export function useGammaMoveScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ watching: number; signals: GammaSignalRow[] }>(
      `${BASE}/scan`,
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: HISTORY_KEY });
    },
  });
}

export function useArmGammaMove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (signalId: string) => api.post<{
      ok: boolean;
      message?: string;
      code?: string;
      status?: string;
      order_id?: string;
      symbol?: string;
      quantity?: number;
      entry?: number;
      stop?: number | null;
      t1?: number;
      t2?: number;
      hold?: string;
    }>(`${BASE}/arm`, { signal_id: signalId }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: HISTORY_KEY });
      if (result.ok) {
        notifyOrder({
          kind: 'info',
          title: 'Gamma entry submitted',
          message: result.symbol
            ? `${result.symbol}: ${result.message || 'entry accepted'}`
            : (result.message || 'Gamma-Unwind entry accepted.'),
        });
      } else {
        notifyOrder({
          kind: 'error',
          title: 'Gamma entry blocked',
          message: result.message || result.code || 'Gamma-Unwind entry was not accepted.',
        });
      }
    },
    onError: (err: any) => notifyOrder({
      kind: 'error',
      title: 'Gamma entry failed',
      message: err?.message || 'Could not submit the Gamma-Unwind entry.',
    }),
  });
}

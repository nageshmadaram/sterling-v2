/** Mirrors the strategy-owned /api/v1/ema-trend contract. Decimal values arrive as strings. */
export type EmaTrendMode = 'swing' | 'intraday' | 'scalping' | 'ultra_scalping';
export type DecimalValue = string | number;
export const EMA_TREND_MODE_LABEL: Record<EmaTrendMode, string> = {
  swing: 'Swing', intraday: 'Intraday', scalping: 'Scalp', ultra_scalping: 'Ultrascalp',
};
export interface EmaTrendProfile {
  mode: EmaTrendMode; signal_minutes: number; context_minutes: number; product: 'CNC' | 'MIS' | 'NRML';
  setup_expiry_bars: number; cooldown_bars: number; min_stop_atr: number; max_stop_atr: number;
  max_chase_atr: number; max_quote_age_s: number; max_hold_bars: number | null;
  entry_start: string; entry_end: string; force_close: string | null; require_green: boolean; chop_filter: boolean;
}
export interface EmaTrendConfig {
  enabled: boolean; auto_execute: boolean; execution_mode: 'PAPER'; modes: EmaTrendMode[]; symbols: string[];
  allocation_inr: DecimalValue; risk_fraction: DecimalValue; strategy_risk_fraction: DecimalValue;
  daily_loss_fraction: DecimalValue; drawdown_fraction: DecimalValue; max_campaigns: number;
  max_sector_campaigns: number; fee_bps: DecimalValue; brokerage_per_order: DecimalValue;
  slippage_bps: DecimalValue; gap_reserve_atr: DecimalValue; fee_schedule: string;
}
export interface EmaTrendDescriptor {
  id: 'ema_trend'; name: string; version: string; rule_hash: string;
  execution_vehicle: 'EQUITY' | 'SELECTED_INSTRUMENT'; profiles: Record<EmaTrendMode, EmaTrendProfile>;
  live_capable: false; blockers: string[];
}
export interface EmaTrendFeature {
  index: number; ready: boolean; context_count: number;
  bar: { open_ms: number; close_ms: number; available_ms: number; open: number; high: number; low: number; close: number; volume: number };
  ema10: number | null; ema21: number | null; dema50: number | null; atr10: number | null;
  st_line: number | null; st_direction: 'UP' | 'DOWN' | 'UNAVAILABLE';
  context_ema10: number | null; context_ema21: number | null; context_st: string; context_st_line?: number | null; context_close_ms: number | null;
  pivots: Array<{ side: string; price: number; occurred_ms: number; confirmed_ms: number }>;
}
export interface EmaTrendContract {
  exchange: string; tradingsymbol: string; instrument_type: 'EQ' | 'FUT' | 'CE' | 'PE'; lot_size: number;
  tick_size: DecimalValue; expiry: string; execution_vehicle: 'EQUITY' | 'FUTURE' | 'OPTION';
  product: 'CNC' | 'MIS' | 'NRML'; capital_model: string; signal_source: string;
}
export interface EmaTrendInstrumentChoice {
  symbol: string; name: string; exchange: string; instrument_type: string; expiry: string; strike?: number;
  lot_size: number; tick_size: DecimalValue; execution_supported: boolean; blocker: string | null;
  contract: EmaTrendContract | null;
}
export interface EmaTrendSetup {
  contract?: EmaTrendContract;
  id: string; symbol: string; mode: EmaTrendMode; index: number; close_ms: number;
  status: 'watching' | 'armed' | 'error' | 'expired'; reason: string; path: string | null;
  stop: DecimalValue | null; ceiling: DecimalValue | null; feature: EmaTrendFeature; config_revision: number;
  activation_eligible?: boolean; watch_only?: boolean;
  signal_source?: string; entry_expires_ms?: number | null; entry_window_open?: boolean; runtime_blockers?: string[];
}
export interface EmaTrendAction {
  id: string; kind: string; side: 'BUY' | 'SELL'; quantity: number; filled_quantity: number;
  status: string; limit: DecimalValue; at_ms: number;
}
export interface EmaTrendCampaign {
  lot_size?: number; instrument_type?: 'EQ' | 'FUT' | 'CE' | 'PE'; expiry?: string;
  id: string; symbol: string; mode: EmaTrendMode; product: 'CNC' | 'MIS' | 'NRML'; tenant_kind: 'SIMULATION' | 'REPLAY';
  revision: number; config_revision: number; config_hash: string; state: string; reason: string; protection: string;
  q0: number; qty: number; vwap: DecimalValue; p0: DecimalValue; r_price: DecimalValue;
  initial_stop: DecimalValue; stop: DecimalValue; budget: DecimalValue; last_mark: DecimalValue;
  last_close_ms: number; below10: number; below21: number; took_4r: boolean;
  realized_net: DecimalValue; total_fees: DecimalValue; pending: EmaTrendAction | null; pending_close?: EmaTrendAction | null; flatten_requested?: boolean;
  lots: Array<{ fill_id: string; qty: number; price: DecimalValue }>;
}
export interface EmaTrendConfigResponse {
  config: EmaTrendConfig; revision: number; activation_ms?: number; paused: boolean;
  defaults: EmaTrendConfig; strategy: EmaTrendDescriptor; profiles: Record<EmaTrendMode, EmaTrendProfile>;
  capabilities: { live: boolean; paper: boolean }; blockers: string[];
}
export interface EmaTrendScan {
  id: string; status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'IDLE';
  progress?: number;
  errors?: Array<string | { reason?: string; symbol?: string; mode?: EmaTrendMode }>;
  results?: Array<{ symbol: string; mode: EmaTrendMode; bars: number; context_bars: number;
    ready: boolean; rows: number; latest_close_ms?: number | null }>;
  created_ms?: number; finished_ms?: number;
}
export interface EmaTrendSupervision {
  status: 'UNKNOWN' | 'STALE' | 'DEGRADED' | 'RUNNING'; age_ms: number | null;
  errors: Array<{ campaign_id?: string; reason: string }>;
}
export interface EmaTrendSnapshot extends Omit<EmaTrendConfigResponse, 'defaults'> {
  supervision?: EmaTrendSupervision;
  rows: EmaTrendSetup[]; campaigns: EmaTrendCampaign[];
  scan: (Partial<EmaTrendScan> & Pick<EmaTrendScan, 'status'>) | null;
  generated_ms?: number; next_cursor?: number | null;
  truncatedModes?: EmaTrendMode[]; failedModes?: EmaTrendMode[];
}
export interface EmaTrendPlan {
  id: string; revision: number; status: 'READY' | 'BLOCKED'; blockers: string[];
  signal_id: string; symbol: string; mode: EmaTrendMode; quantity: number; entry: DecimalValue;
  stop: DecimalValue; budget: DecimalValue; config_revision: number; created_ms: number; expires_ms: number;
}
export interface EmaTrendDatasetManifest {
  content_sha256: string; signal: { bar_count: number; quality_status: string; issues: Array<{ code: string; count: number }> };
  context: { bar_count: number; quality_status: string; issues: Array<{ code: string; count: number }> };
}
export interface EmaTrendReplayReport {
  contract?: EmaTrendContract;
  dataset_manifest?: EmaTrendDatasetManifest;
  id: string; strategy_id: 'ema_trend'; mode: EmaTrendMode; symbol: string;
  evidence_status: string; live_eligible: boolean; config_hash: string; rule_hash: string; dataset_hash: string;
  fill_model: string; limitations: string[];
  campaigns: Array<Partial<EmaTrendCampaign> & { id: string; quantity?: number; current_vwap?: DecimalValue }>;
  fills: Array<{ id: string; campaign_id: string; side: string; quantity?: number; qty?: number; price: DecimalValue; fee?: DecimalValue; fees?: DecimalValue; at_ms?: number }>;
  metrics: { completed_campaigns: number; open_campaigns: number; net_realized: DecimalValue; estimated_fees: DecimalValue; max_drawdown: DecimalValue; ending_equity: DecimalValue; starting_capital: DecimalValue };
}
export interface EmaTrendReplayRun { id: string; status: string; mode: EmaTrendMode; symbol: string; result?: EmaTrendReplayReport; error?: string }

/** Broker-side read-only prerequisite diagnostic, never a live-order approval. */
export interface EmaTrendLiveReadiness {
  status: 'LIVE_BLOCKED';
  live_orders_enabled: false;
  account_id: string | null;
  checks: Record<string, boolean>;
  modes: Partial<Record<EmaTrendMode, { product: 'CNC' | 'MIS'; eligible: false; blockers: string[] }>>;
  blockers: string[];
  requires_operator_certification: boolean;
  read_only: true;
}

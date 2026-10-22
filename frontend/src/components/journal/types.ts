/**
 * Shapes of the /api/v1/journal/* responses (see backend/app/api/v1/endpoints/journal.py).
 *
 * Every number may be null: the journal records what it saw, and a trade that ended outside
 * Sterling has no exit price. Nothing here is ever "0 standing in for unknown".
 */

export type JournalMode = 'live' | 'simulation' | 'all';
export type StrategyId = 'supertrend' | 'snapback' | 'gamma' | 'ema_trend' | 'price_rules';
export type Outcome = 'win' | 'loss' | 'open';
export type TradeModeLabel = 'LIVE' | 'SIMULATION';
export type TradeStatus = 'PENDING' | 'OPEN' | 'CLOSED' | 'REJECTED';

export const STRATEGY_IDS: readonly StrategyId[] = ['supertrend', 'snapback', 'gamma', 'ema_trend', 'price_rules'];

export const STRATEGY_NAMES: Record<StrategyId, string> = {
  supertrend: 'Supertrend',
  snapback: 'Snapback',
  gamma: 'Gamma Unwind',
  ema_trend: 'EMA Trend',
  price_rules: 'Price Rules',
};

export interface JournalScope {
  default_mode: 'LIVE' | 'SIMULATION';
  modes: JournalMode[];
  can_view_all: boolean;
  strategies: StrategyId[];
  exit_reasons: string[];
}

export type TimelineType =
  | 'SIGNAL' | 'ORDER_SUBMITTED' | 'FILLED' | 'PROTECTION_PLACED' | 'TSL_MOVED' | 'TARGET_HIT'
  | 'EXIT_SUBMITTED' | 'EXIT_FILLED' | 'PARTIAL_EXIT' | 'HEDGE_FILLED' | 'REJECTED' | 'FAILED' | 'NOTE';

export interface TimelineEvent {
  type: TimelineType | string;
  ts_ms: number | null;
  price: number | null;
  qty: number | null;
  pnl: number | null;
  detail: string;
  data: Record<string, unknown>;
}

export interface TslMove {
  ts_ms: number | null;
  from: number | null;
  to: number | null;
}

export interface JournalTrade {
  trade_id: string;
  mode: TradeModeLabel;
  strategy: StrategyId | string;
  strategy_label: string;
  status: TradeStatus | string;
  symbol: string;
  underlying: string;
  option_type: string;
  strike: number | null;
  expiry: string;
  side: string;
  lot_size: number | null;
  lots: number | null;
  qty: number | null;
  signal_ts_ms: number | null;
  entry_ts_ms: number | null;
  entry_price: number | null;
  initial_sl: number | null;
  target: number | null;
  current_sl: number | null;
  tsl_history: TslMove[];
  exit_ts_ms: number | null;
  exit_price: number | null;
  exit_qty: number | null;
  exit_reason: string;
  exit_reason_label: string;
  exit_reason_raw?: string;
  gross_pnl: number | null;
  charges: number | null;
  charges_source?: string;
  net_pnl: number | null;
  /** Net when known, else gross (set by the API). */
  pnl: number | null;
  r_multiple: number | null;
  hold_minutes: number | null;
  mae: number | null;
  mfe: number | null;
  capital_used: number | null;
  signal_context: Record<string, unknown>;
  extra: Record<string, unknown>;
  narrative: string;
  source?: string;
  entry_day?: string;
  exit_day?: string;
  trade_day?: string;
  /** Only on /trades/{id}. */
  timeline?: TimelineEvent[];
}

export interface TradesPage {
  mode: string;
  total: number;
  limit: number;
  offset: number;
  items: JournalTrade[];
}

export interface TradeDetail {
  mode: string;
  trade: JournalTrade;
}

export interface TradePick {
  trade_id: string;
  symbol: string;
  strategy: string;
  pnl: number | null;
}

export interface JournalSummary {
  trades: number;
  trades_with_pnl: number;
  unknown_pnl: number;
  open: number;
  pending: number;
  rejected: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate_pct: number | null;
  gross_pnl: number | null;
  charges: number | null;
  net_pnl: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  payoff_ratio: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  best_trade: TradePick | null;
  worst_trade: TradePick | null;
  max_drawdown: number | null;
  avg_hold_minutes: number | null;
  capital_used_avg: number | null;
  capital_used_peak: number | null;
  return_on_capital_pct: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
}

export interface SummaryResponse {
  mode: string;
  summary: JournalSummary;
  by_mode?: Record<string, JournalSummary>;
}

export interface ExitReasonRow {
  reason: string;
  label: string;
  count: number;
  pct_of_trades: number | null;
  wins: number;
  losses: number;
  net_pnl: number | null;
  avg_pnl: number | null;
}

export interface ExitReasonsResponse {
  mode: string;
  reasons: ExitReasonRow[];
}

export interface StrategyRow extends JournalSummary {
  strategy: StrategyId | string;
  label: string;
  biggest_drag: ExitReasonRow | null;
  headline: string;
  rank: number | null;
}

export interface ByStrategyResponse {
  mode: string;
  strategies: StrategyRow[];
  by_mode?: Record<string, StrategyRow[]>;
}

export interface EquityTradePoint {
  ts_ms: number | null;
  day: string;
  trade_id: string;
  strategy: string;
  pnl: number | null;
  cum_pnl: number | null;
}

export interface EquityDayPoint {
  day: string;
  pnl: number | null;
  trades: number;
  cum_pnl: number | null;
}

export interface EquityCurve {
  by_trade: EquityTradePoint[];
  by_day: EquityDayPoint[];
}

export interface EquityResponse extends EquityCurve {
  mode: string;
  by_mode?: Record<string, EquityCurve>;
}

export interface CalendarDay {
  day: string;
  net_pnl: number | null;
  gross_pnl: number | null;
  trades: number;
  wins: number;
  losses: number;
}

export interface CalendarResponse {
  mode: string;
  days: CalendarDay[];
}

export interface SkipItem {
  id: number;
  day: string;
  mode: string;
  strategy: string;
  strategy_label: string;
  symbol: string;
  underlying: string;
  signal_id: string;
  code: string;
  reason: string;
  context: Record<string, unknown>;
  count: number;
  first_ms: number | null;
  last_ms: number | null;
}

export interface SkipCode {
  code: string;
  signals: number;
  times: number;
  reason: string;
}

export interface SkipsResponse {
  mode: string;
  total: number;
  limit: number;
  offset: number;
  items: SkipItem[];
  by_code: SkipCode[];
}

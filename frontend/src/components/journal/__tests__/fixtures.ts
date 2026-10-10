import type {
  ByStrategyResponse, CalendarResponse, EquityResponse, ExitReasonsResponse, JournalScope, JournalSummary,
  JournalTrade, SkipsResponse, StrategyRow, SummaryResponse, TradesPage,
} from '../types';

export const T0 = Date.UTC(2026, 9, 6, 4, 0, 0); // 06 Oct 2026 09:30 IST

export function trade(over: Partial<JournalTrade> = {}): JournalTrade {
  return {
    trade_id: 'SB-1', mode: 'SIMULATION', strategy: 'snapback', strategy_label: 'Snapback', status: 'CLOSED',
    symbol: 'NIFTY2610625000CE', underlying: 'NIFTY', option_type: 'CE', strike: 25000, expiry: '2026-10-06',
    side: 'BUY', lot_size: 75, lots: 1, qty: 75, signal_ts_ms: T0 - 60_000, entry_ts_ms: T0, entry_price: 120,
    initial_sl: 96, target: 150, current_sl: 118, tsl_history: [{ ts_ms: T0 + 600_000, from: 96, to: 118 }],
    exit_ts_ms: T0 + 3_600_000, exit_price: 150, exit_qty: 75, exit_reason: 'TARGET', exit_reason_label: 'Target reached',
    gross_pnl: 2250, charges: 62.5, charges_source: 'estimate', net_pnl: 2187.5, pnl: 2187.5, r_multiple: 1.2,
    hold_minutes: 60, mae: 0, mfe: 2250, capital_used: 9000,
    signal_context: { lane: 'intraday', side: 'fade_down', strength: 'STRONG', stretch: 2.4, distance_pct: 0.8 },
    extra: {}, narrative: 'Bought 1 lot NIFTY 25000 CE at 120.00. Sold at 150.00 on target, +₹2,188 after charges.',
    entry_day: '2026-10-06', exit_day: '2026-10-06', trade_day: '2026-10-06', ...over,
  };
}

export const WIN = trade();

export const LOSS = trade({
  trade_id: 'GU-1', strategy: 'gamma', strategy_label: 'Gamma Unwind', mode: 'LIVE', exit_reason: 'SL',
  exit_reason_label: 'Stop-loss hit', exit_price: 96, gross_pnl: -1800, charges: 60, net_pnl: -1860, pnl: -1860,
  r_multiple: -1.0, hold_minutes: 7, mfe: 300, exit_ts_ms: T0 + 420_000,
  signal_context: { tier: 'ENTRY', oi_drop_pct: 6.2, vol_ratio: 2.1, gates_passed: ['f1', 'f2'], warnings: ['thin book'] },
  narrative: 'Bought 1 lot NIFTY 25000 CE at 120.00. Sold at 96.00 when the stop-loss was hit, -₹1,860 after charges.',
});

export const OPEN = trade({
  trade_id: 'ST-1', strategy: 'supertrend', strategy_label: 'Supertrend', status: 'OPEN', exit_ts_ms: null,
  exit_price: null, exit_reason: '', exit_reason_label: '', gross_pnl: null, charges: null, net_pnl: null, pnl: null,
  r_multiple: null, hold_minutes: null, narrative: 'Bought 1 lot NIFTY 25000 CE at 120.00. Still open.',
  signal_context: { regime: 'BULL', aligned: 3, adx: 28, source: 'derivatives' },
});

export const TIMELINE = [
  { type: 'SIGNAL', ts_ms: T0 - 60_000, price: null, qty: null, pnl: null, detail: 'Snapback signal', data: {} },
  { type: 'ORDER_SUBMITTED', ts_ms: T0 - 30_000, price: 120, qty: 75, pnl: null, detail: 'Buy order placed', data: {} },
  { type: 'FILLED', ts_ms: T0, price: 120, qty: 75, pnl: null, detail: 'Filled at 120.00', data: {} },
  { type: 'PROTECTION_PLACED', ts_ms: T0 + 1000, price: 96, qty: null, pnl: null, detail: 'Stop at 96.00', data: {} },
  { type: 'TSL_MOVED', ts_ms: T0 + 600_000, price: 118, qty: null, pnl: null, detail: 'Stop raised to 118.00', data: {} },
  { type: 'EXIT_FILLED', ts_ms: T0 + 3_600_000, price: 150, qty: 75, pnl: 2250, detail: 'Sold at 150.00', data: {} },
];

export const SCOPE: JournalScope = {
  default_mode: 'LIVE', modes: ['live', 'simulation', 'all'], can_view_all: true,
  strategies: ['supertrend', 'snapback', 'gamma'], exit_reasons: ['SL', 'TSL', 'TARGET'],
};

export function summary(over: Partial<JournalSummary> = {}): JournalSummary {
  return {
    trades: 4, trades_with_pnl: 4, unknown_pnl: 0, open: 1, pending: 0, rejected: 0, wins: 3, losses: 1, breakeven: 0,
    win_rate_pct: 75, gross_pnl: 2600, charges: 240, net_pnl: 2360, avg_win: 1500.5, avg_loss: -1860, profit_factor: 2.42,
    payoff_ratio: 0.81, expectancy: 590, expectancy_r: 0.4,
    best_trade: { trade_id: 'SB-1', symbol: 'NIFTY2610625000CE', strategy: 'snapback', pnl: 2187.5 },
    worst_trade: { trade_id: 'GU-1', symbol: 'NIFTY2610625000CE', strategy: 'gamma', pnl: -1860 },
    max_drawdown: 1860, avg_hold_minutes: 95, capital_used_avg: 9000, capital_used_peak: 12000,
    return_on_capital_pct: 6.5, max_consecutive_wins: 2, max_consecutive_losses: 1, ...over,
  };
}

export const SUMMARY: SummaryResponse = { mode: 'SIMULATION', summary: summary(), by_mode: {} };

export function strategyRow(strategy: string, label: string, over: Partial<StrategyRow> = {}): StrategyRow {
  return { ...summary(), strategy, label, biggest_drag: null, headline: '', rank: null, ...over };
}

export const BY_STRATEGY: ByStrategyResponse = {
  mode: 'SIMULATION',
  strategies: [
    strategyRow('supertrend', 'Supertrend', { trades: 0, wins: 0, losses: 0, net_pnl: 0, win_rate_pct: null, profit_factor: null, expectancy: null }),
    strategyRow('snapback', 'Snapback', { trades: 3, wins: 3, losses: 0, net_pnl: 2400, win_rate_pct: 100, profit_factor: null, expectancy: 800, rank: 1 }),
    strategyRow('gamma', 'Gamma Unwind', {
      trades: 2, wins: 0, losses: 2, net_pnl: -1860, win_rate_pct: 0, profit_factor: 0, expectancy: -930, rank: 2,
      biggest_drag: { reason: 'SL', label: 'Stop-loss hit', count: 2, pct_of_trades: 100, wins: 0, losses: 2, net_pnl: -1860, avg_pnl: -930 },
    }),
  ],
};

export const EXIT_REASONS: ExitReasonsResponse = {
  mode: 'SIMULATION',
  reasons: [
    { reason: 'TARGET', label: 'Target reached', count: 3, pct_of_trades: 60, wins: 3, losses: 0, net_pnl: 4500, avg_pnl: 1500 },
    { reason: 'SL', label: 'Stop-loss hit', count: 2, pct_of_trades: 40, wins: 0, losses: 2, net_pnl: -1860, avg_pnl: -930 },
  ],
};

export const EQUITY: EquityResponse = {
  mode: 'SIMULATION',
  by_trade: [
    { ts_ms: T0, day: '2026-10-06', trade_id: 'SB-1', strategy: 'snapback', pnl: 2187.5, cum_pnl: 2187.5 },
    { ts_ms: T0 + 1000, day: '2026-10-06', trade_id: 'GU-1', strategy: 'gamma', pnl: -1860, cum_pnl: 327.5 },
    { ts_ms: T0 + 86_400_000, day: '2026-10-07', trade_id: 'SB-2', strategy: 'snapback', pnl: 900, cum_pnl: 1227.5 },
  ],
  by_day: [
    { day: '2026-10-06', pnl: 327.5, trades: 2, cum_pnl: 327.5 },
    { day: '2026-10-07', pnl: 900, trades: 1, cum_pnl: 1227.5 },
  ],
};

export const CALENDAR: CalendarResponse = {
  mode: 'SIMULATION',
  days: [
    { day: '2026-10-06', net_pnl: 327.5, gross_pnl: 390, trades: 2, wins: 1, losses: 1 },
    { day: '2026-10-07', net_pnl: -900, gross_pnl: -870, trades: 1, wins: 0, losses: 1 },
  ],
};

export const SKIPS: SkipsResponse = {
  mode: 'SIMULATION', total: 2, limit: 15, offset: 0,
  items: [
    {
      id: 1, day: '2026-10-06', mode: 'SIMULATION', strategy: 'gamma', strategy_label: 'Gamma Unwind', symbol: 'NIFTY',
      underlying: 'NIFTY', signal_id: 's1', code: 'daily_loss_halt', reason: 'The daily loss limit is reached; new entries are halted for today.',
      context: {}, count: 3, first_ms: T0, last_ms: T0 + 60_000,
    },
    {
      id: 2, day: '2026-10-06', mode: 'SIMULATION', strategy: 'supertrend', strategy_label: 'Supertrend', symbol: 'BANKNIFTY',
      underlying: 'BANKNIFTY', signal_id: 's2', code: 'adx_below_min', reason: 'Trend strength (ADX) was below the minimum.',
      context: {}, count: 1, first_ms: T0, last_ms: T0,
    },
  ],
  by_code: [
    { code: 'daily_loss_halt', signals: 1, times: 3, reason: 'The daily loss limit is reached; new entries are halted for today.' },
    { code: 'adx_below_min', signals: 1, times: 1, reason: 'Trend strength (ADX) was below the minimum.' },
  ],
};

export function page(items: JournalTrade[], total = items.length, offset = 0): TradesPage {
  return { mode: 'SIMULATION', total, limit: 25, offset, items };
}

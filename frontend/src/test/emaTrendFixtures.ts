import type { EmaTrendCampaign, EmaTrendConfigResponse, EmaTrendFeature, EmaTrendMode, EmaTrendProfile, EmaTrendReplayReport, EmaTrendSetup, EmaTrendSnapshot } from '../types/emaTrend';

export function emaFeature(): EmaTrendFeature {
  return { index: 260, ready: true, context_count: 104, ema10: 101.125, ema21: 100.75, dema50: 98.625,
    atr10: 2, st_line: 96.5, st_direction: 'UP', context_ema10: 100, context_ema21: 98,
    context_st: 'UP', context_st_line: 92.5, context_close_ms: Date.now() - 86400000,
    pivots: [], bar: { open_ms: Date.now() - 180000, close_ms: Date.now() - 10000, available_ms: Date.now() - 9000,
      open: 100, high: 103, low: 99.5, close: 102, volume: 10000 } };
}
export function emaSetup(mode: EmaTrendMode = 'swing'): EmaTrendSetup {
  return { id: `setup-${mode}`, symbol: 'NSE:TCS', mode, index: 260, close_ms: Date.now() - 10000,
    status: 'armed', reason: 'RETEST_CONFIRMED', path: 'retest', stop: '98', ceiling: '102.5',
    config_revision: 7, activation_eligible: true, feature: emaFeature() };
}
export function emaConfig(): EmaTrendConfigResponse {
  const profile = (mode: EmaTrendMode, signal: number, context: number): EmaTrendProfile => ({
    mode, signal_minutes: signal, context_minutes: context, product: mode === 'swing' ? 'CNC' : 'MIS',
    setup_expiry_bars: 12, cooldown_bars: 3, min_stop_atr: .5, max_stop_atr: 3,
    max_chase_atr: .3, max_quote_age_s: 5, max_hold_bars: mode === 'swing' ? null : 20,
    entry_start: '09:30', entry_end: '14:45', force_close: mode === 'swing' ? null : '15:15',
    require_green: mode !== 'swing', chop_filter: mode !== 'swing',
  });
  const profiles = { swing: profile('swing', 1440, 10080), overnight: profile('overnight', 60, 1440), intraday: profile('intraday', 15, 60),
    scalping: profile('scalping', 3, 15), ultra_scalping: profile('ultra_scalping', 1, 5) };
  const defaults = { enabled: false, auto_execute: false, execution_mode: 'PAPER' as const, modes: ['swing' as const], symbols: [],
    allocation_inr: '0', risk_fraction: '0.0025', strategy_risk_fraction: '0.0075', daily_loss_fraction: '0.01', drawdown_fraction: '0.05',
    max_campaigns: 3, max_sector_campaigns: 2, fee_bps: '10', brokerage_per_order: '0', slippage_bps: '5', gap_reserve_atr: '0.1',
    fee_schedule: 'PAPER_ESTIMATE_V1_NOT_BROKER_VERIFIED' };
  return { revision: 7, activation_ms: Date.now() - 60000, paused: false, defaults,
    config: { ...defaults, symbols: ['NSE:TCS'], allocation_inr: '100000', enabled: true },
    profiles, capabilities: { live: false, paper: true }, blockers: ['ECONOMIC_EVIDENCE_INCONCLUSIVE', 'LIVE_EXECUTION_DISABLED'],
    strategy: { id: 'ema_trend', name: '10/21 EMA + Supertrend', version: '1.0.0', rule_hash: 'rule-fixture',
      execution_vehicle: 'EQUITY', profiles, live_capable: false, blockers: ['LIVE_EXECUTION_DISABLED'] } };
}
export function emaCampaign(mode: EmaTrendMode = 'swing'): EmaTrendCampaign {
  return { id: `campaign-${mode}`, symbol: 'NSE:TCS', mode, product: mode === 'swing' ? 'CNC' : 'MIS', tenant_kind: 'SIMULATION',
    revision: 4, config_revision: 7, config_hash: 'cfg-fixture', state: 'OPEN', reason: 'HOLD', protection: 'PAPER_MONITOR_ONLY',
    q0: 103, qty: 28, vwap: '100', p0: '100', r_price: '5', initial_stop: '95', stop: '99', budget: '600',
    last_mark: '115', last_close_ms: Date.now() - 5000, below10: 0, below21: 1, took_4r: true,
    realized_net: '355.25', total_fees: '19.75', pending: null, lots: [{ fill_id: 'initial', qty: 28, price: '100' }] };
}
export function emaSnapshot(mode: EmaTrendMode = 'swing'): EmaTrendSnapshot {
  const { defaults: _, ...response } = emaConfig();
  return { ...response, rows: [emaSetup(mode)], campaigns: [], scan: null };
}
export function emaReport(mode: EmaTrendMode = 'swing'): EmaTrendReplayReport {
  return { id: 'run-1', strategy_id: 'ema_trend', mode, symbol: 'NSE:TCS', evidence_status: 'INCONCLUSIVE', live_eligible: false,
    config_hash: 'config-7', rule_hash: 'rules-1', dataset_hash: 'data-1', fill_model: 'MODELLED_NEXT_OPEN',
    limitations: ['OHLC_DOES_NOT_PROVE_FILLABILITY'], campaigns: [{ ...emaCampaign(mode), quantity: 28 }],
    fills: [{ id: 'entry', campaign_id: 'campaign-1', side: 'BUY', quantity: 103, price: '100', fee: '10' },
      { id: 'exit-1', campaign_id: 'campaign-1', side: 'SELL', quantity: 25, price: '120', fee: '5' }],
    metrics: { completed_campaigns: 0, open_campaigns: 1, net_realized: '355.25', estimated_fees: '19.75', max_drawdown: '125', ending_equity: '100775.25', starting_capital: '100000' } };
}

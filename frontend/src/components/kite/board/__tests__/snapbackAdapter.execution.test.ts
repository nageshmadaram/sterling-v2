import { describe, expect, it } from 'vitest';
import { snapbackRowToBoard } from '../snapbackAdapter';

function row(mode: 'swing' | 'scalp' | 'intraday', eligible = true): any {
  return {
    signal_id: `sb-${mode}`,
    strategy: 'snapback',
    trading_mode: mode,
    side: 'fade_up',
    symbol: 'NIFTY',
    state: 'armed',
    reason: null,
    execution_eligible: eligible,
    direction: 'BEARISH',
    opt_type: 'PE',
    timestamp_ms: 1_789_009_200_000,
    signal_close_ms: 1_789_009_260_000,
    spot: 24000,
    mean_target: 23800,
    distance_pct: 1,
    stretch: 2,
    level: 24100,
    strength: 'MODERATE',
    realized_vol_pct: 12,
    assumed_iv_pct: 14,
    assumed_vrp: 1.2,
    hold_days: 1,
    contract: {
      symbol: 'NIFTY26OCT24000PE',
      exchange: 'NFO',
      option_type: 'PE',
      strike: 24000,
      expiry: '2026-10-29',
      dte: 20,
      lot_size: 75,
      token: 1,
    },
    premium: 100,
    premium_is_modelled: false,
    quote: { premium: 100, bid: 99.5, ask: 100, ltp: 100, spread_pct: 0.5 },
    stop_premium: 90,
    target_premium: 115,
    trail_premium: null,
    runner_premium: 120,
    profit_lock_premium: 102,
    breakeven_premium: 101,
    trail_points: 2,
    lock_points: 1,
    lots: 1,
    quantity: 75,
    deployed_inr: 7500,
    min_outlay_inr: 7500,
    timeframe_minutes: 1,
    max_hold_bars: 20,
    runner_max_bars: 60,
    round_trip_cost_points: 1,
    fixed_cost_inr: 40,
    planned_risk_inr: 800,
    net_target_inr: 900,
    reasons: ['test'],
    metrics: {},
    historical: false,
    outcome: null,
  };
}

describe('snapback adapter execution eligibility', () => {
  it('preserves backend eligibility for the live scalp lane', () => {
    const mapped = snapbackRowToBoard(row('scalp', true));
    expect(mapped.executionEligible).toBe(true);
  });

  it('keeps intraday research-only even if a stale row says eligible', () => {
    const mapped = snapbackRowToBoard(row('intraday', true));
    expect(mapped.executionEligible).toBe(false);
  });

  it('still refuses a scalp row that backend readiness blocks', () => {
    const mapped = snapbackRowToBoard(row('scalp', false));
    expect(mapped.executionEligible).toBe(false);
  });
});

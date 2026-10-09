import { describe, expect, it } from 'vitest';
import {
  overlaySnapbackLivePosition, snapbackLivePositionToBoard, snapbackRowToBoard,
} from '../snapbackAdapter';

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

function live(over: any = {}): any {
  return {
    position_id: 'SBP-1',
    uid: 'u',
    account_id: 'K1',
    signal_id: 'sb-scalp',
    lane: 'scalp',
    underlying: 'NIFTY',
    side: 'fade_up',
    option_type: 'PE',
    option_exchange: 'NFO',
    option_symbol: 'NIFTY26OCT24000PE',
    option_token: 1,
    lot_size: 25,
    option_qty: 75,
    option_entry_price: 100,
    option_entry_order_id: 'E1',
    option_expiry: '2026-10-29',
    product: 'NRML',
    tick_size: 0.05,
    rules: { target_premium: 115 },
    status: 'OPEN',
    stop_premium: 90,
    desired_stop: 95,
    high_water: 108,
    peak_close: 0,
    is_runner: false,
    phase: 'OPEN_SCALP',
    gtt_id: 77,
    gtt_stop: 95,
    gtt_qty: 50,
    protection_state: 'ACTIVE',
    gtt_pending: false,
    gtt_pending_ms: 0,
    gtt_pending_qty: 0,
    gtt_pending_trigger: 0,
    exit_reason: '',
    option_exit_order_id: '',
    option_exit_requested: 0,
    option_exit_qty: 25,
    option_exit_value: 2500,
    option_exit_price: 100,
    option_exit_unknown_qty: 0,
    realised_pnl: 0,
    flags: [],
    note: '',
    opened_ms: 1_789_009_260_000,
    entry_day: '2026-10-09',
    updated_ms: 1_789_009_300_000,
    protection_pending: false,
    ...over,
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


describe('snapback adapter durable live protection', () => {
  it('uses only a fully synchronized GTT as the active live trail and risk', () => {
    const mapped = snapbackLivePositionToBoard(live());

    expect(mapped.status).toBe('running');
    expect(mapped.levels.stop).toBe(90);
    expect(mapped.levels.trail).toBe(95);
    expect(mapped.sizing.quantity).toBe(50);
    expect(mapped.sizing.atRiskInr).toBe(250);
    expect(mapped.flags?.some((f) => f.label === 'GTT ARMED')).toBe(true);
  });

  it('fails the UI closed when ACTIVE state has the wrong GTT quantity', () => {
    const mapped = snapbackLivePositionToBoard(live({
      gtt_qty: 75,
      protection_pending: true,
    }));

    expect(mapped.levels.trail).toBeNull();
    expect(mapped.sizing.atRiskInr).toBeNull();
    expect(mapped.flags?.some((f) => f.label === 'STOP UNSYNC')).toBe(true);
  });

  it('overlays durable execution truth without losing the current quote/plan context', () => {
    const planned = snapbackRowToBoard(row('scalp', true));
    const mapped = overlaySnapbackLivePosition(planned, live());

    expect(mapped.status).toBe('running');
    expect(mapped.executionEligible).toBe(false);
    expect(mapped.levels.ltp).toBe(100);
    expect(mapped.levels.entry).toBe(100);
    expect(mapped.levels.stop).toBe(90);
    expect(mapped.levels.trail).toBe(95);
    expect(mapped.levels.target).toBe(115);
    expect(mapped.sizing.quantity).toBe(50);
    expect(mapped.sections?.some((s) => s.title === 'Live position & broker protection')).toBe(true);
  });
});

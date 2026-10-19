import { describe, expect, it } from 'vitest';
import { snapbackRowToBoard } from '../snapbackAdapter';

function row(mode: 'swing' | 'scalp' | 'intraday' | 'ultra_scalping' | 'overnight', eligible = true): any {
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

  it.each(['ultra_scalping', 'intraday', 'overnight'] as const)(
    'honors a fresh server eligibility decision for %s', (mode) => {
      expect(snapbackRowToBoard(row(mode, true)).executionEligible).toBe(true);
      expect(snapbackRowToBoard(row(mode, false)).executionEligible).toBe(false);
    },
  );

  it('still refuses a scalp row that backend readiness blocks', () => {
    const mapped = snapbackRowToBoard(row('scalp', false));
    expect(mapped.executionEligible).toBe(false);
  });
});

it('shows the server execution refusal rather than losing it behind the setup reason', () => {
  const source = { ...row('scalp', false), reason: 'Completed reversal',
    execution_block_reason: 'The market is closed.', execution_block_reasons: ['session_closed'] };
  expect(snapbackRowToBoard(source).reason).toBe('The market is closed.');
  delete (source as any).execution_block_reason;
  expect(snapbackRowToBoard(source).reason).toBe('session_closed');
});

import { snapbackRowsToBoard } from '../snapbackAdapter';
const filled = (over: any = {}): any => ({
  position_id: 'plan-1', signal_id: 'sb-scalp', lane: 'scalp', underlying: 'NIFTY',
  option_type: 'PE', option_exchange: 'NFO', option_symbol: 'NIFTY26OCT24000PE',
  option_expiry: '2026-10-29', option_qty: 75, option_entry_price: 103, lot_size: 75,
  option_exit_qty: 0, option_exit_value: 0, option_exit_unknown_qty: 0,
  opened_ms: 1, status: 'OPEN', stop_premium: 90, desired_stop: 105,
  gtt_id: 55, gtt_stop: 105, gtt_qty: 75, protection_state: 'ACTIVE',
  rules: { target_premium: 120 }, exit_reason: '', ...over,
});
it('replaces planned quantity and entry with owned fills, retaining confirmed protection only', () => {
  const [mapped] = snapbackRowsToBoard([row('scalp')], [filled()]);
  expect(mapped.status).toBe('running');
  expect(mapped.levels).toEqual({ ltp: 100, entry: 103, stop: 90, trail: 105, target: 120, exit: null });
  expect(mapped.executionEligible).toBe(false);
  expect(mapped.sizing.quantity).toBe(75);
  expect(mapped.sections[0].title).toBe('Confirmed Snapback position');
});
it('restores a filled position after scanner state is lost without inventing an LTP', () => {
  const [mapped] = snapbackRowsToBoard([], [filled()]);
  expect(mapped.instrument.quoteKey).toBe('NFO:NIFTY26OCT24000PE');
  expect(mapped.levels.ltp).toBeNull();
  expect(mapped.levels.entry).toBe(103);
  expect(mapped.instrument.strike).toBe(24000);
});
it('keeps every owned position and prefers an open holding over closed history for the same signal', () => {
  const positions = [filled(), filled({ position_id: 'closed-plan', status: 'CLOSED', option_entry_price: 99 })];
  const mapped = snapbackRowsToBoard([row('scalp')], positions);
  expect(mapped).toHaveLength(2);
  expect(mapped[0].status).toBe('running');
  expect(mapped[0].levels.entry).toBe(103);
  expect(mapped[1].status).toBe('ended');
  expect(new Set(mapped.map(s => s.id)).size).toBe(2);
});
it('does not represent an entering record as a confirmed fill', () => {
  const [mapped] = snapbackRowsToBoard([], [filled({ status: 'ENTERING', option_entry_price: 0 })]);
  expect(mapped.origin?.label).toBe('OWNED');
  expect(mapped.status).toBe('watching');
  expect(mapped.sizing.quantity).toBeNull();
  expect(mapped.levels.entry).toBeNull();
});
it('does not advertise desired or partially protected stops as active trailing protection', () => {
  const [mapped] = snapbackRowsToBoard([], [filled({ gtt_qty: 30 })]);
  expect(mapped.levels.trail).toBeNull();
  expect(mapped.sizing.atRiskInr).toBeNull();
});
it('uses cumulative confirmed exit value and leaves unpriced external exits missing', () => {
  const pos = filled({ status: 'CLOSED', option_exit_qty: 75, option_exit_value: 8250 });
  expect(snapbackRowsToBoard([], [pos])[0].levels.exit).toBe(110);
  expect(snapbackRowsToBoard([], [{ ...pos, option_exit_unknown_qty: 5 }])[0].levels.exit).toBeNull();
});
it('does not replace a historical model row with a current holding', () => {
  const mapped = snapbackRowsToBoard([{ ...row('scalp'), historical: true }], [filled()]);
  expect(mapped).toHaveLength(2);
  expect(mapped[0].levels.entry).toBe(100);
  expect(mapped[0].historical).toBe(true);
  expect(mapped[1].levels.entry).toBe(103);
});

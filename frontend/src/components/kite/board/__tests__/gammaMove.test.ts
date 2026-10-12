import { describe, it, expect } from 'vitest';
import { gammaMoveToBoard } from '../gammaMoveAdapter';
import type { GammaMoveSnapshot, GammaPositionRow, GammaSignalRow } from '../../../../hooks/useGammaMove';

function signal(over: Partial<GammaSignalRow> = {}): GammaSignalRow {
  return {
    id: 'sig-entry', state: 'armed', tier: 'ENTRY', at_ms: 1000,
    underlying: 'RELIANCE', reason: 'live E1 breakout', warnings: [],
    list_name: 'BREAKOUT-CE', tag: 'HOI-1', strong: false,
    instrument: {
      instrument_id: '222', tradingsymbol: 'RELIANCE26OCT3000CE',
      option_type: 'CE', strike: 3000, expiry: '2026-10-29',
      lot_size: 10, tick_size: 0.05, exchange: 'NFO',
    },
    watch: {
      underlying_token: 111, ref_price: 2980, zone_edge: 3000, atr_pct: 3,
      oi: 10000, candidates_n: 2, dte: 17, build_oi: 10000, chain_ltp: 100,
    },
    metrics: {
      oi_drop_pct: 12, vol_ratio: 2.5, base_low: 99, base_high: 101,
      a: true, b: true, c: true, h: true, strong: false, b_count: 3,
    },
    gates: {
      zone_gap_pct: 0.67, zone_limit_pct: 1.5, avg_vol_lots: 10,
      zero_vol_bars: 0, f1: true, f2: true, unwind: true,
      gate_reasons: [], gate_unknown: [],
      passes: ['zone','liquidity','direction','unwind'],
      tier: 'ENTRY', quadrant: 'SC',
    },
    levels: { ltp: 102, entry: 102, stop: null, t1: 122.4, t2: 142.8 },
    ...over,
  };
}

function snapshot(over: Partial<GammaMoveSnapshot> = {}): GammaMoveSnapshot {
  return {
    day: '2026-10-03',
    today: '2026-10-03',
    historical: false,
    recorded: true,
    source: 'live',
    replay: {},
    coverage: [],
    replay_available: false,
    is_trading_day: true,
    latest_trading_day: '2026-10-03',
    history_days: [],
    strategy: {} as any,
    config: {} as any,
    scan: {},
    session: null,
    candidates: [signal()],
    positions: [],
    record: {} as any,
    mode: { is_paper: true, auto_execute: false, live_execution_enabled: false, note: '' },
    warnings: [],
    blockers: [],
    ...over,
  };
}

function position(over: Partial<GammaPositionRow> = {}): GammaPositionRow {
  return {
    symbol: 'RELIANCE26OCT3000CE', signal_id: 'sig-entry', underlying: 'RELIANCE',
    entry: 102, effective_entry: 102, quantity: 10, lots: 1,
    entered_ms: 1000, entry_day: '2026-10-05', hold: 'swing',
    trade_state: 'OPEN', zone_edge: 3000, base_low: 99, t1: 122.4, t2: 142.8,
    stop: null, sessions_held: 0, swing_armed: false, status: 'open',
    order_id: 'O1', gtt_id: 0, gtt_at: 0, gtt_qty: 0,
    broker_stop: false, stop_mode: 'monitor',
    ...over,
  };
}

describe('gammaMoveToBoard / Gamma Unwind', () => {
  it('returns nothing without a snapshot', () => {
    expect(gammaMoveToBoard(undefined)).toEqual([]);
    expect(gammaMoveToBoard(null)).toEqual([]);
  });

  it('maps ENTRY to an armed long option with four-gate provenance', () => {
    const [row] = gammaMoveToBoard(snapshot());
    expect(row.engine).toBe('gamma_move');
    expect(row.status).toBe('armed');
    expect(row.direction).toBe('long');
    expect(row.instrument.quoteKey).toBe('NFO:RELIANCE26OCT3000CE');
    expect(row.origin?.label).toBe('ENTRY 4/4');
    expect(row.executionEligible).toBe(true);
    expect(row.flags?.some((f) => f.label === '✓ ZONE')).toBe(true);
    expect(row.flags?.some((f) => f.label === 'SC')).toBe(true);
  });

  it('marks reconstructed historical rows as historical', () => {
    const [row] = gammaMoveToBoard(snapshot({
      day: '2026-10-01',
      historical: true,
      source: 'historical_replay',
      candidates: [signal({
        source: 'historical_replay',
        snapshot: { historical_replay: true, replay_resolution: '1minute_close' },
      })],
    }));
    expect(row.executionEligible).toBe(false);
    expect(row.flags?.some((f) => f.label === 'HISTORICAL')).toBe(true);
  });

  it('keeps a recorded historical ENTRY visible but non-executable', () => {
    const [row] = gammaMoveToBoard(snapshot({
      day: '2026-10-02',
      historical: true,
      candidates: [signal()],
    }));
    expect(row.status).toBe('armed');
    expect(row.origin?.label).toBe('ENTRY 4/4');
    expect(row.executionEligible).toBe(false);
  });

  it('maps STRONG WATCHLIST and WATCHLIST as non-executable watching rows', () => {
    const strong = gammaMoveToBoard(snapshot({
      candidates: [signal({
        tier: 'STRONG_WATCHLIST', state: 'watching',
        gates: { ...signal().gates!, passes: ['zone','liquidity','unwind'], tier: 'STRONG_WATCHLIST' },
      })],
    }))[0];
    expect(strong.status).toBe('watching');
    expect(strong.executionEligible).toBe(false);
    expect(strong.origin?.label).toBe('STRONG 3/4');

    const watch = gammaMoveToBoard(snapshot({
      candidates: [signal({
        tier: 'WATCHLIST', state: 'watching',
        gates: { ...signal().gates!, passes: ['zone','liquidity'], tier: 'WATCHLIST' },
      })],
    }))[0];
    expect(watch.status).toBe('watching');
    expect(watch.executionEligible).toBe(false);
    expect(watch.origin?.label).toBe('WATCHLIST');
  });

  it('does not turn an unknown gate into a pass', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [signal({
        tier: 'STRONG_WATCHLIST', state: 'watching',
        gates: {
          ...signal().gates!,
          direction: undefined,
          f1: null, f2: null,
          gate_unknown: ['direction'],
          passes: ['zone','liquidity','unwind'],
          tier: 'STRONG_WATCHLIST',
        } as any,
      })],
    }));
    expect(row.flags?.some((f) => f.label === '? DIRECTION')).toBe(true);
  });

  it('shows the reference swing plan with no invented pre-T1 premium stop', () => {
    const [row] = gammaMoveToBoard(snapshot());
    expect(row.levels.stop).toBeNull();
    expect(row.levels.target).toBe(142.8);
    expect(row.sections.some((s) => s.title === 'Reference trade plan')).toBe(true);
  });

  it('shows DTE<=1 base-low stop for intraday entries', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [signal({
        watch: { ...signal().watch, dte: 1 },
        levels: { ltp: 102, entry: 102, stop: 99, t1: 122.4, t2: 142.8 },
      })],
    }));
    expect(row.levels.stop).toBe(99);
  });

  it('keeps a held position visible when the watchlist row disappears', () => {
    const rows = gammaMoveToBoard(snapshot({
      candidates: [],
      positions: [position({ ltp: 118.5 })],
    }));
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('running');
    expect(rows[0].levels.ltp).toBe(118.5);
    expect(rows[0].reason).toContain('swing');
  });

  it('reports pending reconciliation and synchronized broker stop truthfully', () => {
    const pending = gammaMoveToBoard(snapshot({
      positions: [position({
        status: 'pending', stop: 102, trade_state: 'T1_TRAIL',
        gtt_id: 99, gtt_at: 102, gtt_qty: 10, broker_stop: true,
      })],
    }))[0];
    expect(pending.flags?.some((f) => f.label === 'RECONCILE')).toBe(true);
    expect(pending.flags?.some((f) => f.label === 'GTT ARMED')).toBe(true);
  });
});


describe('gammaMoveToBoard / canonical levels and entry gate', () => {
  const intraday = (over: Partial<GammaSignalRow> = {}) => signal({
    watch: { ...signal().watch, dte: 1 },
    levels: { ltp: 104, ltp_age_s: 2, entry: 102, stop: 99, t1: 122.4, t2: 142.8 },
    ...over,
  });
  const withLots = (lots = 2) => ({ config: { execution_lots: lots } as any });

  it('plans quantity, risk and capital for an ENTRY from configured lots', () => {
    const [row] = gammaMoveToBoard(snapshot({ candidates: [intraday()], ...withLots(2) }));
    expect(row.sizing.lots).toBe(2);
    expect(row.sizing.quantity).toBe(20);
    expect(row.sizing.atRiskInr).toBeCloseTo((102 - 99) * 20);
    expect(row.sizing.deployedInr).toBeCloseTo(102 * 20);
    expect(row.quoteAgeS).toBe(2);
  });

  it('shows no risk for a swing ENTRY: there is no premium SL to honour', () => {
    const [row] = gammaMoveToBoard(snapshot({ candidates: [signal()], ...withLots(1) }));
    expect(row.levels.stop).toBeNull();
    expect(row.sizing.atRiskInr).toBeNull();
    expect(row.sizing.quantity).toBe(10);
  });

  it('does not plan a quantity for alert-only tiers', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [signal({ tier: 'STRONG_WATCHLIST', state: 'watching' })], ...withLots(3),
    }));
    expect(row.sizing.quantity).toBeNull();
  });

  it('states why a live swing ENTRY is not live-eligible', () => {
    const reason = 'Swing hold (expiry more than 1 trading day away). Live Gamma enters intraday contracts only.';
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [signal({ entry_gate: {
        eligible: false, code: 'reference_swing_protection_unavailable', reason, hold: 'swing',
      } })],
    }));
    expect(row.status).toBe('armed');
    expect(row.reason).toBe(reason);
    expect(row.executionEligible).toBe(false);
    const flag = row.flags?.find((f) => f.label === 'SWING · NOT LIVE');
    expect(flag?.hint).toBe(reason);
  });

  it('marks an expired E1 as ended instead of an armed ticket', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [signal({ entry_gate: {
        eligible: false, code: 'signal_stale', reason: 'This E1 signal is no longer current.', hold: 'intraday',
      } })],
    }));
    expect(row.status).toBe('ended');
    expect(row.flags?.some((f) => f.label === 'EXPIRED')).toBe(true);
    expect(row.executionEligible).toBe(false);
  });

  it('labels an eligible intraday ENTRY with its hold', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [intraday({ entry_gate: { eligible: true, code: '', reason: '', hold: 'intraday' } })],
    }));
    expect(row.executionEligible).toBe(true);
    expect(row.flags?.some((f) => f.label === 'INTRADAY')).toBe(true);
  });

  it('separates SL (hard stop at entry) from TSL (stop after T1) on a held position', () => {
    const pre = gammaMoveToBoard(snapshot({
      candidates: [], positions: [position({ hold: 'intraday', stop: 99, base_low: 99, ltp: 110 })],
    }))[0];
    expect(pre.levels.stop).toBe(99);
    expect(pre.levels.trail).toBeNull();

    const post = gammaMoveToBoard(snapshot({
      candidates: [], positions: [position({
        hold: 'intraday', stop: 102, base_low: 99, trade_state: 'T1_TRAIL', ltp: 125,
      })],
    }))[0];
    expect(post.levels.stop).toBe(99);       // the original SL is not overwritten
    expect(post.levels.trail).toBe(102);     // TSL sits at entry
    expect(post.sizing.atRiskInr).toBe(0);   // stop at entry: nothing at risk
  });

  it('does not present an unsynchronized broker TSL as active protection', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [],
      positions: [position({
        hold: 'intraday',
        stop: 102,
        base_low: 99,
        trade_state: 'T1_TRAIL',
        stop_mode: 'both',
        broker_stop: false,
        gtt_id: 0,
        gtt_at: 0,
        gtt_qty: 0,
        ltp: 125,
      })],
    }));

    expect(row.levels.stop).toBe(99);       // strategy's original hard-stop plan
    expect(row.levels.trail).toBeNull();    // no broker-confirmed TSL
    expect(row.sizing.atRiskInr).toBeNull();
    expect(row.flags?.some((f) => f.label === 'STOP UNSYNC')).toBe(true);

    const plan = row.sections[0];
    expect(plan.stats.find((x) => x.label === 'Desired stop')?.value).toBe('102.00');
    expect(plan.stats.find((x) => x.label === 'Broker stop')?.value).toBe('—');
  });

  it('uses the broker-confirmed Gamma TSL for active protection and risk', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [],
      positions: [position({
        hold: 'intraday',
        stop: 102,
        base_low: 99,
        trade_state: 'T1_TRAIL',
        stop_mode: 'both',
        broker_stop: true,
        gtt_id: 77,
        gtt_at: 102,
        gtt_qty: 10,
        ltp: 125,
      })],
    }));

    expect(row.levels.trail).toBe(102);
    expect(row.sizing.atRiskInr).toBe(0);
    expect(row.flags?.some((f) => f.label === 'GTT ARMED')).toBe(true);

    const plan = row.sections[0];
    expect(plan.stats.find((x) => x.label === 'Desired stop')?.value).toBe('102.00');
    expect(plan.stats.find((x) => x.label === 'Broker stop')?.value).toBe('102.00');
  });

  it('computes unrealised P&L from LTP and booked partials, canonical labels only', () => {
    const [row] = gammaMoveToBoard(snapshot({
      candidates: [], positions: [position({
        hold: 'intraday', stop: 99, base_low: 99, ltp: 112, partial_realised_inr: -40,
      })],
    }));
    const plan = row.sections[0];
    const label = (name: string) => plan.stats.find((x) => x.label === name)?.value;
    expect(label('P&L')).toBe('+₹60.00');          // (112-102)*10 - 40
    expect(label('Entry')).toBe('102.00');
    expect(label('SL')).toBe('99.00');
    expect(label('Qty')).toBe('10');
    expect(label('Risk')).toBe('₹30.00');
    expect(label('Capital')).toBe('₹1,020.00');
    expect(plan.stats.some((x) => /Active stop/i.test(x.label))).toBe(false);
  });
});

it('keeps reconstructed candidates read-only even in a mixed live snapshot', () => {
  const [row] = gammaMoveToBoard(snapshot({ candidates: [signal({
    source: 'historical_replay',
    entry_gate: { eligible: true, code: 'eligible', reason: '', hold: 'intraday' },
  })] }));
  expect(row.historical).toBe(true);
  expect(row.executionEligible).toBe(false);
});

it('uses a bearish thesis for a bought put without changing its owned entry path', () => {
  const [row] = gammaMoveToBoard(snapshot({ candidates: [signal({
    instrument: { ...signal().instrument, option_type: 'PE', tradingsymbol: 'RELIANCE26OCT3000PE' },
  })] }));
  expect(row.direction).toBe('short');
  expect(row.instrument.quoteKey).toBe('NFO:RELIANCE26OCT3000PE');
});

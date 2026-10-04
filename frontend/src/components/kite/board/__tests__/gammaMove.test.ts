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

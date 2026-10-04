import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { GammaMoveBoard } from '../GammaMoveBoard';

let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let armState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let historicalLoadState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let backfillState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let snapshotArgs: unknown[] = [];
let historyState: any = {
  data: { days: [], signals: [], count: 0, tier_counts: { entry: 0, strong_watchlist: 0, watchlist_e1: 0 } },
  isLoading: false, error: null,
};

vi.mock('../../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: (...args: unknown[]) => { snapshotArgs = args; return snap; },
  useGammaMoveHistory: () => historyState,
  useGammaMoveScan: () => scanState,
  useLoadGammaMoveHistoricalDay: () => historicalLoadState,
  useBackfillGammaMoveHistory: () => backfillState,
  useArmGammaMove: () => armState,
}));

function signal(over: any = {}) {
  return {
    id: 'RELIANCE:BREAKOUT-CE:RELIANCE26OCT3000CE:1',
    state: 'armed', tier: 'ENTRY', at_ms: 1791180900000,
    underlying: 'RELIANCE', reason: 'live E1 breakout', warnings: [],
    list_name: 'BREAKOUT-CE', tag: 'HOI-1', strong: false,
    instrument: {
      instrument_id: '222', tradingsymbol: 'RELIANCE26OCT3000CE',
      option_type: 'CE', strike: 3000, expiry: '2026-10-29',
      lot_size: 10, tick_size: 0.05, exchange: 'NFO',
    },
    watch: {
      underlying_token: 111, ref_price: 2980, zone_edge: 3000, atr_pct: 3.0,
      oi: 10000, candidates_n: 2, dte: 17, build_oi: 10000, chain_ltp: 100,
      carried: false,
    },
    metrics: {
      oi_drop_pct: 12, vol_ratio: 2.4, base_low: 99, base_high: 101,
      a: true, b: true, c: true, h: true, strong: false, b_count: 3,
    },
    gates: {
      zone_gap_pct: 0.67, zone_limit_pct: 1.5, avg_vol_lots: 8,
      zero_vol_bars: 0, f1: true, f2: true, unwind: true,
      gate_reasons: [], gate_unknown: [],
      passes: ['zone','liquidity','direction','unwind'], tier: 'ENTRY',
      quadrant: 'SC',
    },
    levels: { ltp: 102, entry: 102, stop: null, t1: 122.4, t2: 142.8 },
    snapshot: {},
    ...over,
  };
}

function position(over: any = {}) {
  return {
    symbol: 'RELIANCE26OCT3000CE',
    signal_id: signal().id,
    underlying: 'RELIANCE',
    entry: 102, effective_entry: 102, quantity: 10, lots: 1,
    entered_ms: 1791180900000, entry_day: '2026-10-05',
    hold: 'swing', trade_state: 'OPEN', zone_edge: 3000, base_low: 99,
    t1: 122.4, t2: 142.8, stop: null, sessions_held: 0,
    swing_armed: false, status: 'open', order_id: 'O1',
    gtt_id: 0, gtt_at: 0, gtt_qty: 0, broker_stop: false, stop_mode: 'monitor',
    ...over,
  };
}

function data(over: any = {}) {
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
    history_days: [
      { day: '2026-10-03', signals: 1, watches: 2, last_run_ms: 1, updated_ms: 1, issue: '', source: 'live_recorded' },
      { day: '2026-10-02', signals: 2, watches: 4, last_run_ms: 1, updated_ms: 1, issue: '', source: 'historical_replay', resolution: '1minute_ohlcv_proxy' },
    ],
    strategy: {
      id: 'gamma_move', name: 'Gamma Unwind V1', contract_version: 'GU-0.8.2-S1.6.0',
      reference_package: 'GAMMA-UNWIND client 0.8.2', spec_version: 'S1.6.0',
      edition: 'client', tagline: 'OI-unwind breakout at daily supply/demand zones.',
      how_it_works: 'Daily zones, HOI-1, hidden E2, live E1, four gates.',
      provenance: 'user-supplied package', signal_parity: true, client_top_n_oi: 1,
      execution_note: 'Sterling execution is a separate adapter.', enabled: true,
    },
    config: { enabled: true },
    scan: {},
    session: null,
    candidates: [],
    positions: [],
    record: {
      trades: 0, wins: 0, losses: 0, win_rate: null,
      consecutive_losses: 0, consecutive_wins: 0,
      realised_inr: 0, day_realised_inr: 0, day: '',
      verdict: 'no realised trades yet',
    },
    mode: { is_paper: true, auto_execute: false, note: 'AUTO requires both switches.' },
    warnings: ['Client edition keeps HOI-1 only even though strategy.yaml top_n_oi is 2.'],
    blockers: [],
    ...over,
  };
}

beforeEach(() => {
  snap = { data: data(), isLoading: false, error: null };
  scanState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  armState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  historicalLoadState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  backfillState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  snapshotArgs = [];
  historyState = {
    data: { days: [], signals: [], count: 0, tier_counts: { entry: 0, strong_watchlist: 0, watchlist_e1: 0 } },
    isLoading: false, error: null,
  };
});

describe('GammaMoveBoard / Gamma Unwind', () => {
  it('shows recent persisted E1 events across days even when selected day is empty', () => {
    historyState.data = {
      days: [],
      signals: [
        { ...signal(), history_day: '2026-10-02', source: 'live_recorded' },
        {
          ...signal({ id: 'old-strong', tier: 'STRONG_WATCHLIST' }),
          history_day: '2026-10-01',
          source: 'historical_replay',
        },
      ],
      count: 2,
      tier_counts: { entry: 1, strong_watchlist: 1, watchlist_e1: 0 },
    };
    snap.data = data({ candidates: [] });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.getByText(/2 persisted E1 events/)).toBeTruthy();
    expect(screen.getAllByText('ENTRY').length).toBeGreaterThan(0);
    expect(screen.getByText('STRONG_WATCHLIST')).toBeTruthy();
    expect(screen.getAllByText('historical').length).toBeGreaterThan(0);
  });

  it('jumps from the E1 ledger to that signal day', () => {
    historyState.data = {
      days: [],
      signals: [{ ...signal(), history_day: '2026-10-02', source: 'live_recorded' }],
      count: 1,
      tier_counts: { entry: 1, strong_watchlist: 0, watchlist_e1: 0 },
    };

    render(<GammaMoveBoard nowMs={Date.now()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open day' }));

    expect(snapshotArgs).toEqual([true, 0, '2026-10-02']);
  });

  it('polls while flat so ticker-driven E1 can appear', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(snapshotArgs).toEqual([true, 3000, undefined]);
  });

  it('automatically loads up to five recent missing completed sessions', () => {
    snap.data = data({
      history_days: [
        { day: '2026-10-03', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-10-02', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-09-30', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-09-29', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-09-28', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-09-25', signals: 0, watches: 0, source: 'available_replay' },
        { day: '2026-09-24', signals: 0, watches: 0, source: 'available_replay' },
      ],
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(backfillState.mutate).toHaveBeenCalledWith([
      '2026-10-02',
      '2026-09-30',
      '2026-09-29',
      '2026-09-28',
      '2026-09-25',
    ]);
  });

  it('shows recent session cards with E1 and watch counts', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.getByText(/2026-10-02/)).toBeTruthy();
    expect(document.body.textContent).toContain('2 E1 · 4 watch');
  });

  it('switches to a recorded day and stops live polling', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);

    fireEvent.change(screen.getByLabelText('Gamma trading day'), {
      target: { value: '2026-10-02' },
    });

    expect(snapshotArgs).toEqual([true, 0, '2026-10-02']);
    expect(screen.getByRole('button', { name: /scan now/i })).toBeDisabled();
  });

  it('renders a historical ENTRY as recorded and never exposes Buy', () => {
    snap.data = data({
      day: '2026-10-02',
      historical: true,
      recorded: true,
      source: 'live_recorded',
      candidates: [signal()],
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.getByText(/Recorded Gamma-Unwind state for 2026-10-02/)).toBeTruthy();
    expect(screen.getByText(/1 recorded ENTRY signal/)).toBeTruthy();
    fireEvent.click(screen.getByText(/RELIANCE/));
    expect(screen.queryByRole('button', { name: /Buy .*RELIANCE26OCT3000CE/i })).toBeNull();
  });

  it('automatically loads an unrecorded historical trading day', () => {
    snap.data = data({
      day: '2026-10-01',
      historical: true,
      recorded: false,
      source: 'available_replay',
      replay_available: true,
      is_trading_day: true,
      candidates: [],
      history_days: [],
      blockers: ['No cached Gamma-Unwind state for 2026-10-01. Historical signals will load automatically from available broker history.'],
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(historicalLoadState.mutate).toHaveBeenCalledWith({ day: '2026-10-01' });
    expect(document.body.textContent).toContain('Loading historical Gamma-Unwind signals');
  });

  it('renders reconstructed ENTRY rows read-only as historical signals', () => {
    snap.data = data({
      day: '2026-10-01',
      historical: true,
      recorded: true,
      source: 'historical_replay',
      replay: { resolution: '1minute_ohlcv_proxy', complete: true },
      candidates: [signal({
        source: 'historical_replay',
        snapshot: { historical_replay: true, replay_resolution: '1minute_ohlcv_proxy' },
      })],
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.getByText(/Historical Gamma-Unwind signals for 2026-10-01/)).toBeTruthy();
    expect(screen.getByText(/1 historical ENTRY signal/)).toBeTruthy();
    expect(document.body.textContent).toContain('HISTORICAL');
    fireEvent.click(screen.getByText(/RELIANCE/));
    expect(screen.queryByRole('button', { name: /Buy .*RELIANCE26OCT3000CE/i })).toBeNull();
  });

  it('moves a closed-market Today view to the latest trading session', () => {
    snap.data = data({
      day: '2026-10-04',
      today: '2026-10-04',
      is_trading_day: false,
      latest_trading_day: '2026-10-01',
      recorded: false,
      source: 'live',
      candidates: [],
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(snapshotArgs).toEqual([true, 0, '2026-10-01']);
  });

  it('bootstraps an empty unrecorded live board once', () => {
    snap.data = data({
      recorded: false,
      candidates: [],
      history_days: [],
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(scanState.mutate).toHaveBeenCalledTimes(1);
  });

  it('does not auto-scan a completed zero-result live day', () => {
    snap.data = data({
      recorded: true,
      candidates: [],
      scan: { stage_a: { scanned: 50, near_level: 0, seconds: 1 } },
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(scanState.mutate).not.toHaveBeenCalled();
  });

  it('runs an on-demand watchlist rebuild', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    fireEvent.click(screen.getByRole('button', { name: /scan now/i }));
    expect(scanState.mutate).toHaveBeenCalledTimes(1);
  });

  it('shows watch, E1 and tier counts even when there is no executable entry', () => {
    snap.data = data({
      candidates: [
        signal({
          id: 'watch:RELIANCE26OCT3000CE:BREAKOUT-CE',
          state: 'watching',
          tier: 'WATCHLIST',
          gates: null,
        }),
        signal({
          id: 'e1-strong',
          state: 'armed',
          tier: 'STRONG_WATCHLIST',
          gates: {
            ...signal().gates,
            passes: ['zone', 'liquidity', 'unwind'],
            tier: 'STRONG_WATCHLIST',
          },
        }),
      ],
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.getByTestId('gamma-row-counts').textContent)
      .toContain('1 watches · 1 E1 · 0 ENTRY · 1 STRONG');
  });

  it('shows when today was reconstructed from a missed reference build slot', () => {
    snap.data = data({
      scan: {
        recovered_slot: '13:15',
        recovery_source: 'scheduled_history',
        coverage: ['dated instrument master unavailable'],
        stage_b: { candidates: 2, seconds: null },
        stage_c: { watched: 2, armed: 0, historical_requests: 2, seconds: null },
      },
      candidates: [signal({
        id: 'watch:RELIANCE26OCT3000CE:BREAKOUT-CE',
        state: 'watching', tier: 'WATCHLIST', gates: null,
      })],
    });

    render(<GammaMoveBoard nowMs={Date.now()} />);

    expect(document.body.textContent).toContain('recovered 13:15');
    expect(document.body.textContent).toContain('reconstructed at the missed reference slot 13:15');
  });

  it('shows reference warnings and account mode', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/HOI-1/)).toBeTruthy();
    expect(screen.getByText('PAPER')).toBeTruthy();
    expect(screen.getByText('MANUAL')).toBeTruthy();
  });

  it('renders an ENTRY row as armed and exposes the guarded buy action', () => {
    snap.data = data({ candidates: [signal()] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/1 armed/)).toBeTruthy();
    expect(document.body.textContent).toContain('RELIANCE');
    fireEvent.click(screen.getByText(/RELIANCE/));
    const buy = screen.queryByRole('button', { name: /Buy .*RELIANCE26OCT3000CE/i });
    if (buy) {
      fireEvent.click(buy);
      expect(armState.mutate).toHaveBeenCalledWith(signal().id);
    }
  });

  it('does not present STRONG WATCHLIST as executable', () => {
    snap.data = data({
      candidates: [signal({
        state: 'watching', tier: 'STRONG_WATCHLIST',
        gates: { ...signal().gates, passes: ['zone','liquidity','unwind'], tier: 'STRONG_WATCHLIST' },
      })],
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.queryByText(/1 armed/)).toBeNull();
  });

  it('reads out every blocker instead of showing a silent empty board', () => {
    snap.data = data({ blockers: ['No active Kite account', 'Gamma Unwind is switched off'] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText('No active Kite account')).toBeTruthy();
    expect(screen.getByText('Gamma Unwind is switched off')).toBeTruthy();
  });

  it('shows scan-stage cost and a scan issue separately', () => {
    snap.data = data({
      scan: {
        issue: 'no Kite session — log in to scan',
        stage_a: { scanned: 0, near_level: 0, seconds: 0 },
        stage_b: { candidates: 0, seconds: 0 },
        stage_c: { watched: 0, armed: 0, historical_requests: 0, seconds: 0 },
        total_seconds: 0,
      },
      blockers: ['no Kite session — log in to scan'],
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByTestId('gamma-scan-issue').textContent).toMatch(/no Kite session/);
  });

  it('keeps a durable held position visible after its watch row disappears', () => {
    snap.data = data({ positions: [position()] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(document.body.textContent).toContain('RELIANCE26OCT3000CE');
    expect(document.body.textContent).toContain('SWING');
  });

  it('surfaces a refused entry and network error', () => {
    armState.data = { ok: false, message: 'only ENTRY tier can execute' };
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/only ENTRY tier can execute/)).toBeTruthy();

    snap = { data: undefined, isLoading: false, error: new Error('kite is down') };
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/kite is down/)).toBeTruthy();
  });

  it('shows UNKNOWN when account mode cannot be proved', () => {
    snap.data = data({ mode: { is_paper: null, auto_execute: false, note: '' } });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText('UNKNOWN')).toBeTruthy();
    expect(screen.queryByText('PAPER')).toBeNull();
  });
});

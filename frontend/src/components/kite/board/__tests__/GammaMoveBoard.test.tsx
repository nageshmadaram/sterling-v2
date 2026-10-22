import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { GammaMoveBoard } from '../GammaMoveBoard';

// 11:00 IST on a trading day: the 15:20 intraday cutoff must not depend on when
// the suite runs.
const MORNING = Date.UTC(2026, 9, 5, 5, 30);

const replayClock = vi.hoisted(() => ({ now: null as number | null }));
vi.mock('../../../../hooks/useReplayStore', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../hooks/useReplayStore')>(),
  useSimNowMs: () => replayClock.now,
}));

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

vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));

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
    mode: {
      is_paper: true,
      auto_execute: false,
      live_execution_enabled: false,
      note: 'AUTO requires both switches.',
    },
    warnings: ['Client edition keeps HOI-1 only even though strategy.yaml top_n_oi is 2.'],
    blockers: [],
    ...over,
    config: {
      enabled: true, stop_mode: 'both', execution_lots: 1, ...(over.config ?? {}),
    },
  };
}

beforeEach(() => {
  replayClock.now = null;
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

function renderEvidence(ui: import('react').ReactElement) {
 const result=render(ui);
 // Status text is inline now; there is no info panel to open.
 return result;
}

describe('GammaMoveBoard / Gamma Unwind', () => {

  it('never exposes the generic trade action on a historical day', async () => {
    snap = {
      data: data({
        day: '2026-10-02',
        today: '2026-10-04',
        historical: true,
        recorded: true,
        source: 'historical_replay',
        candidates: [signal({ id: 'hist-entry', tier: 'ENTRY', state: 'armed' })],
      }),
      isLoading: false, error: null,
    };

    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);

    expect(screen.queryByText(/^Buy\b/i)).toBeNull();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it('polls while flat so ticker-driven E1 can appear', () => {
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
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

    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);

    expect(backfillState.mutate).toHaveBeenCalledWith([
      '2026-10-02',
      '2026-09-30',
      '2026-09-29',
      '2026-09-28',
      '2026-09-25',
    ]);
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

    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);

    expect(snapshotArgs).toEqual([true, 0, '2026-10-01']);
  });

  it('bootstraps an empty unrecorded live board once', () => {
    snap.data = data({
      recorded: false,
      candidates: [],
      history_days: [],
    });

    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);

    expect(scanState.mutate).toHaveBeenCalledTimes(1);
  });

  it('does not auto-scan a completed zero-result live day', () => {
    snap.data = data({
      recorded: true,
      candidates: [],
      scan: { stage_a: { scanned: 50, near_level: 0, seconds: 1 } },
    });

    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);

    expect(scanState.mutate).not.toHaveBeenCalled();
  });

  it('renders an ENTRY row with only the guarded strategy-owned buy action', () => {
    snap.data = data({ candidates: [signal()] });
    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    expect(screen.getByText(/1 armable of 1 ENTRY signal/)).toBeTruthy();
    expect(document.body.textContent).toContain('RELIANCE');
    fireEvent.click(screen.getByText(/RELIANCE/));

    // The shared detail ticket is read-only: /kite/orders belongs to the
    // SuperTrend manual lifecycle, not Gamma-Unwind.
    expect(screen.queryByText(/Quote & sizing preview/i)).toBeNull();
    expect(screen.queryByText(/Place BUY CALL/i)).toBeNull();

    const buy = screen.getByRole('button', { name: /Buy 10 RELIANCE OCT 3000 CE/i });
    fireEvent.click(buy);
    expect(armState.mutate).toHaveBeenCalledWith(signal().id);
  });

  it('disables the dedicated Buy action when live entry transport is blocked', () => {
    snap.data = data({
      candidates: [signal()],
      mode: {
        is_paper: false,
        auto_execute: true,
        live_execution_enabled: false,
        note: 'AUTO armed; operator master switch is off.',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const blocked = screen.getByRole('button', { name: /Entry blocked/i });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute('title', expect.stringContaining('Live trading is switched off'));
    fireEvent.click(blocked);
    expect(armState.mutate).not.toHaveBeenCalled();
    expect(screen.getByText(/LIVE ENTRY BLOCKED/)).toBeInTheDocument();
  });



  it('blocks live swing ENTRY before the click because no broker premium stop exists', () => {
    snap.data = data({
      candidates: [signal({ watch: { ...signal().watch, dte: 17 } })],
      config: { enabled: true, stop_mode: 'both' },
      mode: {
        is_paper: false,
        auto_execute: true,
        live_execution_enabled: true,
        note: 'live floor enabled',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const blocked = screen.getByRole('button', { name: /Entry blocked/i });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute(
      'title',
      expect.stringContaining('Swing trades are not allowed live'),
    );
    fireEvent.click(blocked);
    expect(armState.mutate).not.toHaveBeenCalled();
  });

  it('blocks live intraday ENTRY when stop mode is monitor-only', () => {
    snap.data = data({
      candidates: [signal({
        watch: { ...signal().watch, dte: 1 },
        levels: { ...signal().levels, stop: 99 },
      })],
      config: { enabled: true, stop_mode: 'monitor' },
      mode: {
        is_paper: false,
        auto_execute: true,
        live_execution_enabled: true,
        note: 'live floor enabled',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const blocked = screen.getByRole('button', { name: /Entry blocked/i });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute(
      'title',
      expect.stringContaining('requires broker or both stop mode'),
    );
    expect(armState.mutate).not.toHaveBeenCalled();
  });



  it('blocks live intraday ENTRY at the 15:20 IST cutoff before submission', () => {
    snap.data = data({
      candidates: [signal({
        watch: { ...signal().watch, dte: 1 },
        levels: { ...signal().levels, stop: 99 },
      })],
      config: { enabled: true, stop_mode: 'both' },
      mode: {
        is_paper: false,
        auto_execute: true,
        live_execution_enabled: true,
        note: 'live floor enabled',
      },
    });

    const afterCutoff = Date.UTC(2026, 9, 5, 9, 51); // 15:21 IST
    renderEvidence(<GammaMoveBoard nowMs={afterCutoff} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const blocked = screen.getByRole('button', { name: /Entry blocked/i });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute(
      'title',
      expect.stringContaining('15:20 IST'),
    );
    expect(armState.mutate).not.toHaveBeenCalled();
  });

  it('allows a live intraday ENTRY only with broker-backed protection and live floor on', () => {
    snap.data = data({
      candidates: [signal({
        watch: { ...signal().watch, dte: 1 },
        levels: { ...signal().levels, stop: 99 },
      })],
      config: { enabled: true, stop_mode: 'both' },
      mode: {
        is_paper: false,
        auto_execute: true,
        live_execution_enabled: true,
        note: 'live floor enabled',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const buy = screen.getByRole('button', { name: /Buy 10 RELIANCE OCT 3000 CE/i });
    expect(buy).not.toBeDisabled();
    fireEvent.click(buy);
    expect(armState.mutate).toHaveBeenCalledWith(signal().id);
  });

  it('blocks ENTRY when broker mode cannot be proved', () => {
    snap.data = data({
      candidates: [signal()],
      mode: {
        is_paper: null,
        auto_execute: true,
        live_execution_enabled: true,
        note: 'mode unknown',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    fireEvent.click(screen.getByText(/RELIANCE/));

    const blocked = screen.getByRole('button', { name: /Entry blocked/i });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute('title', 'Broker not connected.');
    expect(armState.mutate).not.toHaveBeenCalled();
  });

  it('shows the server reason a live swing ENTRY is not live-eligible', () => {
    const reason = 'Swing hold (expiry more than 1 trading day away). Live Gamma enters intraday contracts only.';
    snap.data = data({
      candidates: [signal({
        entry_gate: {
          eligible: false, code: 'reference_swing_protection_unavailable',
          reason, hold: 'swing',
        },
      })],
      mode: {
        is_paper: false, auto_execute: true, live_execution_enabled: true, note: '',
      },
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);
    expect(screen.getByText(/ENTRY signal.* · execution blocked/)).toBeTruthy();
    fireEvent.click(screen.getByText(/RELIANCE/));

    expect(screen.getByRole('button', { name: /Entry blocked/i })).toBeDisabled();
    expect(screen.getByTestId('gamma-entry-block-reason').textContent).toBe(reason);
    expect(armState.mutate).not.toHaveBeenCalled();
  });

  it('does not offer an expired E1 as an armed entry ticket', () => {
    snap.data = data({
      candidates: [signal({
        entry_gate: {
          eligible: false, code: 'signal_stale',
          reason: 'This E1 signal is no longer current.', hold: 'intraday',
        },
      })],
    });

    renderEvidence(<GammaMoveBoard nowMs={MORNING} />);

    expect(screen.queryByText(/armable/)).toBeNull();
    expect(screen.queryByText(/execution blocked/)).toBeNull();
  });

  it('does not present STRONG WATCHLIST as executable', () => {
    snap.data = data({
      candidates: [signal({
        state: 'watching', tier: 'STRONG_WATCHLIST',
        gates: { ...signal().gates, passes: ['zone','liquidity','unwind'], tier: 'STRONG_WATCHLIST' },
      })],
    });
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.queryByText(/1 armed/)).toBeNull();
  });

  it('reads out every blocker instead of showing a silent empty board', () => {
    snap.data = data({ blockers: ['No active Kite account', 'Gamma Unwind is switched off'] });
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
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
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByTestId('gamma-scan-issue').textContent).toMatch(/no Kite session/);
  });

  it('keeps a durable held position visible after its watch row disappears', () => {
    snap.data = data({ positions: [position()] });
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    // The row renders the underlying and the short contract (expiry + strike).
    expect(document.body.textContent).toContain('RELIANCE');
    expect(document.body.textContent).toContain('OCT3000CE');
    fireEvent.click(screen.getAllByText(/RELIANCE/)[0]);
    // Gamma runs its original rules: no trading-mode tag on its rows (modes are SuperTrend and Snapback only).
    expect(screen.queryByTitle('Trading mode')).toBeNull();
  });

  it('surfaces a refused entry and network error', () => {
    armState.data = { ok: false, message: 'only ENTRY tier can execute' };
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/only ENTRY tier can execute/)).toBeTruthy();

    snap = { data: undefined, isLoading: false, error: new Error('kite is down') };
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/kite is down/)).toBeTruthy();
  });

  it('does not repeat an unknown account mode in the board footer', () => {
    snap.data = data({ mode: { is_paper: null, auto_execute: false, note: '' } });
    renderEvidence(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.queryByText('UNKNOWN')).not.toBeInTheDocument();
    expect(screen.queryByText('PAPER')).toBeNull();
  });
});

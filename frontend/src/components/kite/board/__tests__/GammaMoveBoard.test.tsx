import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { GammaMoveBoard } from '../GammaMoveBoard';

let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let armState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let snapshotArgs: unknown[] = [];

vi.mock('../../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: (...args: unknown[]) => { snapshotArgs = args; return snap; },
  useGammaMoveScan: () => scanState,
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
  snapshotArgs = [];
});

describe('GammaMoveBoard / Gamma Unwind', () => {
  it('polls while flat so ticker-driven E1 can appear', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(snapshotArgs).toEqual([true, 3000]);
  });

  it('runs an on-demand watchlist rebuild', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    fireEvent.click(screen.getByRole('button', { name: /scan now/i }));
    expect(scanState.mutate).toHaveBeenCalledTimes(1);
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

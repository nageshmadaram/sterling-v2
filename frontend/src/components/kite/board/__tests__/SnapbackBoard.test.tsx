/**
 * The Snapback board: the scorecard, the daily-cadence note, and the empty state.
 *
 * The load-bearing assertion is the scorecard. This engine passes six of nine
 * gate checks and misses two sample-size ones, and a flat "NOT VALIDATED" banner
 * would read identically to a strategy whose entries lose money. Those are not
 * the same thing to someone deciding whether to click Buy, so the board has to
 * say which checks passed and why the others did not.
 *
 * The second is the cadence note. Every rule here reads a daily CLOSE, so a
 * board watched through the session does not move — and without saying so, quiet
 * reads as broken.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { SnapbackBoard } from '../SnapbackBoard';
import { useReplayStore } from '../../../../hooks/useReplayStore';

const updateMode = vi.fn();
let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null };
let executeState: any = { mutate: vi.fn(), isPending: false, variables: undefined };

let historyState: any = { data: { sessions: 30, signals: [], count: 0 } };

vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));

vi.mock('../../../../hooks/useSnapback', () => ({
  useStopSnapbackScan: () => ({ mutate: updateMode, isPending: false }),
  useSnapbackSnapshot: () => snap,
  useRunSnapbackScan: () => scanState,
  useExecuteSnapbackSignal: () => executeState,
  useSnapbackHistory: () => historyState,
}));

const VALIDATION = {
  promoted: false, measured_at: '2026-09-12',
  span: '2023-09-13..2026-09-11 (739 sessions)',
  universe: ['NIFTY', 'RELIANCE'],
  oos_trades: 229, oos_entry_days: 121, oos_mean_day_return_pct: 6.282,
  oos_ci_pct: [-1.71, 17.17], sharpe: 1.595, deflated_sharpe: 0.0443,
  permutation_p: 0.0287, permutation_p_full_sample: 0.0012,
  breakeven_vrp: 2.243, max_drawdown_pct: -10.38, allocation_pct: 2.0,
  total_return_pct: 36.96,
  per_year_pct: { 2024: 14.55, 2025: 5.48, 2026: 6.9 },
  checks: {
    enough_trades: true, enough_days: true, profitable: true,
    beats_random_timing: true, deflated_sharpe: false, priced_edge: true,
    consistent_across_years: true, survivable_drawdown: true,
    mean_proven: false,
  },
  reasons: [
    'deflated Sharpe 0.044 < 0.5 — the result does not survive how many variants were tried',
    'day-clustered 95% interval [-1.71%, +17.17%] includes zero — the DIRECTION is established, the SIZE is not',
  ],
  slippage_pct: 0.5, passed: 7, total_checks: 9,
};

function row(over: any = {}) {
  return {
    signal_id: 'snapback:fade_up:NIFTY:1757000000000',
    strategy: 'snapback', side: 'fade_up', symbol: 'NIFTY', state: 'armed',
    reason: null, direction: 'BEARISH', opt_type: 'PE',
    timestamp_ms: 1_789_009_200_000, spot: 23437, mean_target: 23100,
    distance_pct: 1.44, stretch: 2.6, level: 23380, strength: 'MODERATE',
    realized_vol_pct: 11.4, assumed_iv_pct: 13.9, assumed_vrp: 1.22,
    hold_days: 10, underlying_token: 256265,
    contract: {
      symbol: 'NIFTY25OCT23800PE', strike: 23800, option_type: 'PE',
      expiry: '2026-10-28', dte: 36, lot_size: 75, token: 1, exchange: 'NFO',
      delta: -0.7, moneyness: 'ITM',
    },
    premium: 520.5, premium_is_modelled: false, modelled_premium: 511,
    quote: { premium: 520.5, bid: 519, ask: 522, ltp: 520, oi: 1, spread_pct: 0.58, blockers: [] },
    stop_premium: 338.3, target_premium: 690, trail_premium: null,
    outcome: null, lots: 1, quantity: 75, deployed_inr: 39037.5,
    reasons: ['Closed above its 20-session high (23,380.00).'],
    metrics: { lookback_days: 20 },
    ...over,
  };
}

function snapshot(over: any = {}) {
  return {
    strategy: { name: 'Snapback', validated: false, validation: VALIDATION },
    config: { enabled: true },
    rows: [row()], armed: 1, scanned: 19, scanning: false,
    last_scan_ms: 1_789_009_200_000, last_error: null, failures: [],
    warnings: [], auto_execution_blocker: null, catchup_sessions: 3,
    capabilities: {
      live_scan: true, historical_model: true, replay: true,
      auto_execution: {
        available: false,
        reason: 'Snapback live position and broker protection are not connected. No automatic broker entry is sent.',
      },
      manual_execution: {
        single_leg: false, spread: false, hedge: false,
        protection_lifecycle: false,
        reason: 'Snapback strategy-owned manual execution/protection is not connected; generic Kite orders use the SuperTrend protection lifecycle',
      },
    },
    ...over,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  snap = { data: snapshot(), isLoading: false, error: null };
  scanState = { mutate: vi.fn(), isPending: false, error: null };
  executeState = { mutate: vi.fn(), isPending: false, variables: undefined };
  historyState = { data: { sessions: 30, signals: [], count: 0 } };
});

function renderEvidence(ui: import('react').ReactElement) {
 const result=render(ui);
 // Status text is inline now; there is no info panel to open.
 return result;
}

describe('Snapback board', () => {
  it('reports the scorecard, not a bare verdict', () => {
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByText(/PASSES 7 OF 9 CHECKS/)).toBeInTheDocument();
    expect(screen.getByText(/✓ Beats random entry timing/)).toBeInTheDocument();
    expect(screen.getByText(/✗ Survives the variant count/)).toBeInTheDocument();
    expect(screen.getByText(/✗ Size of the edge proven/)).toBeInTheDocument();
  });

  it('gives the harness’s own reasons verbatim', () => {
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByText(/the DIRECTION is established, the SIZE is not/))
      .toBeInTheDocument();
  });

  it('states the break-even vol multiple against what the market charges', () => {
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByText(/2.243x/)).toBeInTheDocument();
    expect(screen.getByText(/1.15–1.30x/)).toBeInTheDocument();
  });

  it('says the rows change once a session, so quiet does not read as broken', () => {
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.queryByRole('button', { name: 'Snapback help ↗' })).toBeNull();
  });

  it('explains an empty board rather than showing nothing', () => {
    snap = { data: snapshot({ rows: [], armed: 0 }), isLoading: false, error: null };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/No signals in the recent completed sessions/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Snapback help ↗' })).toBeNull();
  });

  it('surfaces a failed scan instead of an empty-board explanation', () => {
    snap = {
      data: snapshot({ rows: [], last_error: 'No active Kite account' }),
      isLoading: false, error: null,
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/No active Kite account/)).toBeInTheDocument();
  });

  it('surfaces a failed scan even when stale rows remain visible', () => {
    snap = {
      data: snapshot({ last_error: 'No active Kite account' }),
      isLoading: false,
      error: null,
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/Last scan failed: No active Kite account/)).toBeInTheDocument();
  });

  it('shows the auto-execution blocker when there is one', () => {
    snap = {
      data: snapshot({ auto_execution_blocker: 'Snapback did not pass the harness' }),
      isLoading: false, error: null,
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/TRADING BLOCKED/)).toBeInTheDocument();
    expect(screen.getByText(/did not pass the harness/)).toBeInTheDocument();
    expect(screen.getByText(/Automatic execution is blocked.*live position and broker protection/i)).toBeInTheDocument();
    expect(screen.getByText(/Manual execution is blocked.*strategy-owned/i)).toBeInTheDocument();
  });

  it('never reports AUTO available when validation clears but execution is disconnected', () => {
    snap = {
      data: snapshot({ auto_execution_blocker: null }),
      isLoading: false,
      error: null,
    };

    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);

    expect(screen.getByText(/TRADING BLOCKED/)).toBeInTheDocument();
    expect(screen.queryByText(/AUTO TRADING READY/)).toBeNull();
    expect(screen.queryByText(/ALGO ELIGIBLE/)).toBeNull();
  });

  it('keeps the expanded ticket read-only when Snapback execution is unavailable', () => {
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);

    const underlying = screen.getAllByText(/NIFTY/)[0];
    act(() => { underlying.click(); });

    expect(screen.queryByText(/Quote & sizing preview/i)).toBeNull();
    expect(screen.queryByText(/Place BUY PUT/i)).toBeNull();
  });
  it('uses the Snapback plan/arm executor instead of the generic order ticket', () => {
    snap = {
      data: snapshot({
        rows: [row({ execution_eligible: true })],
        capabilities: {
          live_scan: true, historical_model: true, replay: true,
          auto_execution: { available: false, enabled: false, reason: '', blockers: [], session: [] },
          manual_execution: {
            single_leg: true, spread: false, hedge: false,
            protection_lifecycle: true, reason: '',
          },
        },
      }),
      isLoading: false, error: null,
    };

    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });

    expect(screen.queryByText(/Quote & sizing preview/i)).toBeNull();
    expect(screen.queryByText(/Place BUY PUT/i)).toBeNull();

    screen.getByRole('button', { name: 'Execute Snapback' }).click();
    expect(executeState.mutate).toHaveBeenCalledWith('snapback:fade_up:NIFTY:1757000000000');
  });

  it('fails closed when execution capability metadata is absent', () => {
    const base = snapshot();
    delete base.capabilities;
    snap = { data: base, isLoading: false, error: null };

    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);

    expect(screen.getByText(/execution unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/open a row to buy it/i)).toBeNull();
  });

  it('does not claim manual execution when the configured hedge cannot use the manual ticket', () => {
    snap = {
      data: snapshot({
        auto_execution_blocker: 'Snapback did not pass the harness',
        capabilities: {
          live_scan: true, historical_model: true, replay: true,
          auto_execution: {
            available: false,
            reason: 'Snapback live position and broker protection are not connected. No automatic broker entry is sent.',
          },
          manual_execution: {
            single_leg: false, spread: false, hedge: false,
            protection_lifecycle: false,
            reason: 'manual live execution does not place or protect the configured index-futures hedge',
          },
        },
      }),
      isLoading: false, error: null,
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/TRADING BLOCKED/)).toBeInTheDocument();
    expect(screen.getByText(/Manual execution is blocked.*index-futures hedge/))
      .toBeInTheDocument();
    expect(screen.queryByText(/BUY BY HAND ONLY/)).toBeNull();
  });

  it('counts the instruments that could not be scanned rather than dropping them', () => {
    snap = {
      data: snapshot({ failures: ['TCS: no daily candles'] }),
      isLoading: false, error: null,
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/1 instrument could not be scanned/))
      .toBeInTheDocument();
  });

  it('shows replayed history so a quiet board is not a broken one', () => {
    historyState = {
      data: {
        sessions: 30, count: 2,
        signals: [row({ signal_id: 'h1', historical: true, state: 'ended' }),
                  row({ signal_id: 'h2', historical: true, state: 'ended' })],
      },
    };
    snap = { data: snapshot({ rows: [], armed: 0 }), isLoading: false, error: null };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.queryByRole('button', { name: 'Snapback help ↗' })).toBeNull();
    // The empty-state text must NOT appear when history filled the board.
    expect(screen.queryByText(/deliberately rare/)).toBeNull();
  });

  it('keeps historical outcome rows even when a live row shares the signal id', () => {
    historyState = {
      data: {
        sessions: 30,
        count: 1,
        signals: [row({ historical: true, state: 'ended' })],
      },
    };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getAllByText(/NIFTY/).length).toBeGreaterThan(1);
  });

  it('keeps daily evidence/history out of scalp while exposing the live scalp lane', () => {
    snap.data = snapshot({
      config: { enabled: true, trading_mode: 'scalp', scalp_timeframe_minutes: 1 },
      rows: [row({
        trading_mode: 'scalp',
        execution_eligible: true,
        signal_close_ms: 1_789_009_260_000,
        timeframe_minutes: 1,
        max_hold_bars: 20,
        runner_max_bars: 60,
        planned_risk_inr: 900,
      })],
      capabilities: {
        live_scan: true, historical_model: false, replay: false, option_tape_replay: true,
        auto_execution: { available: true, enabled: true, reason: '', blockers: [], session: [] },
        manual_execution: {
          single_leg: true, spread: false, hedge: false,
          protection_lifecycle: true, reason: '',
        },
      },
    });
    historyState.data = { sessions: 30, count: 1, signals: [row({ historical: true, state: 'ended' })] };
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);

    expect(screen.getByText(/1-minute bars · scalp/)).toBeInTheDocument();
    expect(screen.getByText('AUTO TRADING READY')).toBeInTheDocument();
    expect(screen.getByText(/1 armed — open a row to buy it/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Snapback help ↗' })).toBeNull();
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByRole('button', { name: 'Execute Snapback' })).toBeEnabled();
    expect(screen.queryByText('TEST MODE · NO REAL ORDERS')).toBeNull();
    expect(screen.queryByText(/PASSES 7 OF 9/)).toBeNull();
    expect(screen.queryByText(/once a session and not once a tick/)).toBeNull();
    expect(screen.queryByText(/rows marked/)).toBeNull();
  });

  it('permits intraday only when the server confirms execution and the signal is eligible', () => {
    snap.data = snapshot({
      config: { enabled: true, trading_mode: 'intraday', scalp_timeframe_minutes: 5 },
      rows: [row({ trading_mode: 'intraday', execution_eligible: true, signal_close_ms: 1_789_009_500_000 })],
      capabilities: {
        live_scan: true, historical_model: false, replay: false, option_tape_replay: true,
        auto_execution: { available: true, enabled: true, reason: '', blockers: [], session: [] },
        manual_execution: {
          single_leg: true, spread: false, hedge: false,
          protection_lifecycle: true, reason: '',
        },
      },
    });
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);

    expect(screen.getByText(/5-minute bars · intraday/)).toBeInTheDocument();
    expect(screen.queryByText('TEST MODE · NO REAL ORDERS')).toBeNull();
    expect(screen.getByText('AUTO TRADING READY')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Snapback help ↗' })).toBeNull();
    expect(screen.getByText(/open a row to buy it/i)).toBeInTheDocument();
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByRole('button', { name: 'Execute Snapback' })).toBeEnabled();
  });

  it('keeps ineligible intraday signals read-only even when execution capability is available', () => {
    snap.data = snapshot({
      config: { enabled: true, trading_mode: 'intraday', scalp_timeframe_minutes: 5 },
      rows: [row({ trading_mode: 'intraday', execution_eligible: false, signal_close_ms: 1_789_009_500_000 })],
      capabilities: {
        live_scan: true, historical_model: false, replay: false, option_tape_replay: true,
        auto_execution: { available: true, enabled: true, reason: '', blockers: [], session: [] },
        manual_execution: {
          single_leg: true, spread: false, hedge: false,
          protection_lifecycle: true, reason: '',
        },
      },
    });
    renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
    act(() => { screen.getAllByText(/NIFTY/)[0].click(); });
    expect(screen.getByRole('button', { name: 'Execute Snapback' })).toBeDisabled();
  });
});


it('groups replay rows against the replay clock', () => {
  const wall = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-09-14T12:00:00+05:30'));
  const original = useReplayStore.getState().status;
  snap.data = snapshot({ rows: [row({ state: 'running', timestamp_ms: Date.parse('2026-09-11T15:30:00+05:30') })] });
  const { container, rerender } = renderEvidence(<SnapbackBoard />);
  const headings = () => Array.from(container.querySelectorAll('.sb-day')).map(n => n.textContent);
  const before = headings();
  try {
    act(() => useReplayStore.setState({ status: {
      ...original, state: 'running', current_date: '2026-09-11', current_time_iso: '12:00:00',
      config: { ...original.config, date: '2026-09-11' } as any,
    } }));
    rerender(<SnapbackBoard />);
    expect(headings()).not.toEqual(before);
    expect(screen.queryByText('Today')).toBeNull();
    expect(screen.queryByText('Yesterday')).toBeNull();
  } finally {
    act(() => useReplayStore.setState({ status: original }));
    wall.mockRestore();
  }
});
it('labels clock pauses separately from execution faults', () => {
  const data = snapshot();
  data.capabilities.auto_execution.session = ['cash_signal_auction'];
  snap = { data, isLoading: false, error: null };
  renderEvidence(<SnapbackBoard />);
  expect(screen.getByText('MARKET CLOSED')).toBeInTheDocument();
  expect(screen.queryByText('TRADING BLOCKED')).not.toBeInTheDocument();
});

it('displays visible trading mode badges on snapback rows', () => {
  snap = {
    data: snapshot({
      rows: [
        row({ signal_id: 'sig-swing', trading_mode: 'swing', state: 'armed' }),
      ],
    }),
    isLoading: false,
    error: null,
  };
  renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
  expect(screen.getAllByText('Swing')[0]).toBeInTheDocument();
});

it('keeps manual execution and Buy action available during session pause', () => {
  const data = snapshot({
    rows: [
      row({ signal_id: 'sig-paused', trading_mode: 'swing', state: 'armed', execution_eligible: true }),
    ],
  });
  data.capabilities.auto_execution.session = ['session_closed'];
  data.capabilities.manual_execution.single_leg = true;
  data.capabilities.manual_execution.reason = '';
  snap = { data, isLoading: false, error: null };
  renderEvidence(<SnapbackBoard nowMs={1_789_009_200_000} />);
  expect(screen.getByText('MARKET CLOSED')).toBeInTheDocument();
  expect(screen.getByText(/after-market order/)).toBeInTheDocument();
  const buyBtn = screen.getByRole('button', { name: /Execute Snapback/i });
  expect(buyBtn).not.toBeDisabled();
});


/**
 * Every board shows the same columns.
 *
 * This is the property the boards kept losing: each used to name its own column
 * list, and the board dropped any column no row could fill. The result was that
 * switching tabs moved the stop, renamed the exit, and made you re-find every
 * number.
 *
 * Asserted against the real adapters of the three release strategies
 * (SuperTrend, Gamma Unwind, Snapback) with real-shaped data, so it fails if an
 * engine quietly starts asking for its own set again.
 */
import { describe, it, expect, vi } from 'vitest';
import { ROW_METRICS } from '../signalRowSpec';

// BoardTicket pulls a live quote; the parity being tested is structural.
vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));
import { render, screen } from '@testing-library/react';
import React from 'react';
import { SignalBoard, BOARD_COLUMNS, DEFAULT_HIDDEN_COLUMNS, visibleColumns, COLUMNS, type ColumnId } from '../SignalBoard';
import type { BoardSignal } from '../boardTypes';
import { BoardTicket } from '../BoardTicket';
import { supertrendToBoard } from '../supertrendAdapter';
import { gammaMoveToBoard } from '../gammaMoveAdapter';
import { snapbackRowToBoard } from '../snapbackAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';
import type { GammaMoveSnapshot } from '../../../../hooks/useGammaMove';
import type { SnapbackRow } from '../../../../hooks/useSnapback';
import { SnapbackBoard } from '../SnapbackBoard';
import { GammaMoveBoard } from '../GammaMoveBoard';
import { SuperTrendSharedBoard } from '../../SuperTrendSharedBoard';
import { useKiteSettings } from '../../../../store/useKiteSettings';

vi.mock('../../../../hooks/useSnapback', () => ({
  useSnapbackSnapshot: () => ({ data: { config: { enabled: true }, rows: [sbRow], warnings: [], failures: [] } }),
  useSnapbackHistory: () => ({ data: { signals: [] } }),
  useStopSnapbackScan: () => ({ mutate: vi.fn(), isPending: false }),
  useRunSnapbackScan: () => ({ mutate: vi.fn() }),
  useExecuteSnapbackSignal: () => ({ mutate: vi.fn() }),
}));
vi.mock('../../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: () => ({ data: gammaSnapshot }),
  useGammaMoveHistory: () => ({ data: { signals: [] } }),
  useGammaMoveScan: () => ({ mutate: vi.fn() }),
  useLoadGammaMoveHistoricalDay: () => ({ mutate: vi.fn() }),
  useBackfillGammaMoveHistory: () => ({ mutate: vi.fn() }),
  useArmGammaMove: () => ({ mutate: vi.fn() }),
}));

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

const stLeg: OptionLeg = {
  moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY26AUG24000CE',
  strike: 24000, expiry: '2026-08-27', lot_size: 75,
  premium_spot: 200, entry_sl: 160, premium_sl: 185, is_active: true,
};
const stRow: EngineSignalRow = {
  underlying: 'NIFTY 50', token: 1, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
  legs: [stLeg], spot: 24100, stop_loss: 24000, score: 82,
  timestamp_ms: NOW, is_active: true, is_fresh: true, source: 'spot',
};

// Gamma Unwind: an armed ENTRY candidate, fresh and live.
const gammaSnapshot = {
  day: '2026-08-21', today: '2026-08-21', historical: false, recorded: true, source: 'live',
  replay: {}, coverage: [], replay_available: false, is_trading_day: true,
  latest_trading_day: '2026-08-21', history_days: [], strategy: {}, config: { execution_lots: 1 },
  scan: {}, session: null, positions: [], record: {}, warnings: [], blockers: [],
  mode: { is_paper: false, auto_execute: false, live_execution_enabled: true, note: '' },
  candidates: [{
    id: 'sig-entry', state: 'armed', tier: 'ENTRY', at_ms: NOW,
    underlying: 'RELIANCE', reason: 'live E1 breakout', warnings: [],
    list_name: 'BREAKOUT-CE', tag: 'HOI-1', strong: false,
    instrument: {
      instrument_id: '222', tradingsymbol: 'RELIANCE26AUG3000CE', option_type: 'CE',
      strike: 3000, expiry: '2026-08-27', lot_size: 10, tick_size: 0.05, exchange: 'NFO',
    },
    watch: {
      underlying_token: 111, ref_price: 2980, zone_edge: 3000, atr_pct: 3,
      oi: 10000, candidates_n: 2, dte: 6, build_oi: 10000, chain_ltp: 100,
    },
    metrics: {
      oi_drop_pct: 12, vol_ratio: 2.5, base_low: 99, base_high: 101,
      a: true, b: true, c: true, h: true, strong: false, b_count: 3,
    },
    gates: {
      zone_gap_pct: 0.67, zone_limit_pct: 1.5, avg_vol_lots: 10, zero_vol_bars: 0,
      f1: true, f2: true, unwind: true, gate_reasons: [], gate_unknown: [],
      passes: ['zone', 'liquidity', 'direction', 'unwind'], tier: 'ENTRY', quadrant: 'SC',
    },
    levels: { ltp: 102, entry: 102, stop: null, t1: 122.4, t2: 142.8 },
  }],
} as unknown as GammaMoveSnapshot;

// Snapback: an armed daily fade with a real (not modelled) premium.
const sbRow = {
  signal_id: 'snapback:fade_up:NIFTY:1', strategy: 'snapback', side: 'fade_up', symbol: 'NIFTY',
  state: 'armed', reason: null, direction: 'BEARISH', opt_type: 'PE', timestamp_ms: NOW,
  spot: 23437, mean_target: 23100, distance_pct: 1.44, stretch: 2.6, level: 23380,
  strength: 'MODERATE', realized_vol_pct: 11.4, assumed_iv_pct: 13.9, assumed_vrp: 1.22,
  hold_days: 10, underlying_token: 256265,
  contract: {
    symbol: 'NIFTY26SEP23800PE', strike: 23800, option_type: 'PE', expiry: '2026-09-29',
    dte: 36, lot_size: 75, token: 1234, exchange: 'NFO', delta: -0.7, moneyness: 'ITM',
  },
  premium: 520.5, premium_is_modelled: false, modelled_premium: 511,
  quote: { premium: 520.5, bid: 519, ask: 522, ltp: 520, oi: 120000, spread_pct: 0.58, blockers: [] },
  stop_premium: 338.3, target_premium: 690, trail_premium: null, runner_premium: null, outcome: null,
  lots: 1, quantity: 75, deployed_inr: 39037.5, min_outlay_inr: 39037.5,
  reasons: ['Closed above its 20-session high.'], metrics: { stretch_atr: 2.6, lookback_days: 20 },
} as unknown as SnapbackRow;

/** Same shape every board is mounted with. */
const boards: Record<string, BoardSignal[]> = {
  supertrend: supertrendToBoard([stRow]),
  gamma_move: gammaMoveToBoard(gammaSnapshot),
  snapback: [snapbackRowToBoard(sbRow)],
};

const headersOf = (signals: BoardSignal[], hidden: ReadonlySet<ColumnId>) => {
  const { container } = render(
    <SignalBoard
      signals={signals}
      columns={BOARD_COLUMNS}
      hidden={hidden}
      openId={null}
      onToggle={() => {}}
      nowMs={NOW}
    />,
  );
  return [...container.querySelectorAll('.sb-head')].map((h) => h.textContent!.trim());
};

const DEFAULTS = new Set<ColumnId>(DEFAULT_HIDDEN_COLUMNS);

describe('actual strategy components share persisted layout and actions', () => {
  const mountBoards = () => render(<>
    <SuperTrendSharedBoard rows={[stRow]} nowMs={NOW} onSelectSignal={() => {}} onOpenChart={() => {}} />
    <SnapbackBoard nowMs={NOW} onOpenChart={() => {}} />
    <GammaMoveBoard nowMs={NOW} onOpenChart={() => {}} />
  </>);
  const layouts = (container: HTMLElement) => [...container.querySelectorAll('.sb-head-row')]
    .map(row => [...row.querySelectorAll('.sb-head')].map(h => ({ text: h.textContent, width: (h as HTMLElement).style.width })));
  it('mounts all three with identical canonical header geometry and compact Buy controls', () => {
    const view = mountBoards();
    const headers = layouts(view.container);
    expect(headers).toHaveLength(3);
    expect(headers[1]).toEqual(headers[0]);
    expect(headers[2]).toEqual(headers[0]);
    const actions = [...view.container.querySelectorAll('.sb-row [data-cell="trade"] button')];
    expect(actions).toHaveLength(3);
    for (const action of actions) {
      expect(action.textContent).toBe('B');
      expect((action as HTMLElement).style.width).toBe('22px');
    }
  });
  it('applies one changed order and visibility preference to all actual boards', () => {
    const before = useKiteSettings.getState();
    try {
      useKiteSettings.setState({ signalLeftColumnOrder: [...before.signalLeftColumnOrder].reverse(), hiddenSignalCols: [...before.hiddenSignalCols, 'ltp'] });
      const view = mountBoards();
      const headers = layouts(view.container);
      expect(headers).toHaveLength(3);
      expect(headers[1]).toEqual(headers[0]);
      expect(headers[2]).toEqual(headers[0]);
      expect(headers[0].map(h => h.text)).not.toContain('LTP');
      view.unmount();
    } finally {
      useKiteSettings.setState({ signalLeftColumnOrder: before.signalLeftColumnOrder, hiddenSignalCols: before.hiddenSignalCols });
    }
  });
  it('puts strategy controls before the rows and uses the same contract row styling', () => {
    const { container } = mountBoards();
    const rows = [...container.querySelectorAll('.sb-row:not(.sb-parent)')] as HTMLElement[];
    expect(rows).toHaveLength(3);
    expect(rows.map(row => row.style.background)).toEqual([rows[0].style.background, rows[0].style.background, rows[0].style.background]);
    expect(rows.map(row => row.style.borderBottom)).toEqual([rows[0].style.borderBottom, rows[0].style.borderBottom, rows[0].style.borderBottom]);
    for (const name of ['Snapback', 'Gamma Unwind']) {
      const footer = screen.getByRole('region', { name: `${name} controls and information` });
      const parent = footer.parentElement!;
      const header = parent.querySelector('.sb-head-row') || parent.parentElement?.querySelector('.sb-head-row');
      expect(header).toBeTruthy();
      expect(header!.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
      expect(footer.querySelector('button')).toBeTruthy();
    }
  });
});

describe('column parity across engines', () => {
  it('gives every board the same headers, in the same order', () => {
    const [first, ...rest] = Object.entries(boards).map(([name, signals]) => [name, headersOf(signals, DEFAULTS)] as const);
    for (const [name, headers] of rest) {
      expect(headers, `${name} vs ${first[0]}`).toEqual(first[1]);
    }
  });

  it('opens on SuperTrend’s own column names, in its order', () => {
    // Labels and widths come from signalRowSpec, the table SuperTrend's rows
    // use, so a cell sits in the same place whichever board you are on.
    // The standard collapsed row: what a trader acts on. The rest is in the
    // expanded row and the column picker.
    expect(headersOf(boards.supertrend, DEFAULTS)).toEqual([
      'Instrument', 'Status', 'LTP', 'Entry (Δpts)', 'SL', 'TSL', 'P&L', 'Time', 'Trade',
    ]);
  });

  it('keeps a column an engine cannot fill, rather than differing', () => {
    expect(headersOf(boards.supertrend, new Set<ColumnId>())).toContain('TSL');
  });

  it('offers the same extras to every board', () => {
    const none = new Set<ColumnId>();
    for (const [name, signals] of Object.entries(boards)) {
      const headers = headersOf(signals, none);
      for (const extra of ['Qty', 'Risk', 'Score']) {
        expect(headers, `${name} missing ${extra}`).toContain(extra);
      }
    }
  });

  it('names every canonical column, so none is unreachable', () => {
    const known = new Set(COLUMNS.map((c) => c.id));
    for (const id of BOARD_COLUMNS) expect(known, id).toContain(id);
    for (const id of DEFAULT_HIDDEN_COLUMNS) expect(BOARD_COLUMNS, id).toContain(id);
  });

  it('hides the engine tag on a single-engine board and shows it on a mixed one', () => {
    // The one column that still comes and goes, because on a single-engine
    // board it repeats the same three letters down every row.
    const mixed = [...boards.supertrend, ...boards.snapback];
    expect(visibleColumns(boards.supertrend, BOARD_COLUMNS).map((c) => c.id)).not.toContain('engine');
    expect(visibleColumns(mixed, BOARD_COLUMNS).map((c) => c.id)).toContain('engine');
  });
});

describe('expanded-row parity across engines', () => {
  // The expanded row is where the boards diverged most: SuperTrend's leg
  // opened onto a calculator and a QuoteDetail with Buy and Sell, while the
  // others showed two read-only cards and no way to act. They now mount the
  // same BoardTicket.
  const expand = (signals: BoardSignal[]) => {
    const parent = signals[0];
    const target = parent.children?.[0] ?? parent;
    const { container } = render(
      <SignalBoard
        signals={signals}
        columns={BOARD_COLUMNS}
        hidden={DEFAULTS}
        openId={target.id}
        onToggle={() => {}}
        onToggleGroup={() => {}}
        renderDetail={(sig) => <BoardTicket signal={sig} />}
        nowMs={NOW}
      />,
    );
    return container;
  };

  it.each(Object.keys(boards))('%s expands onto the shared ticket', (name) => {
    const container = expand(boards[name]);
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent!.trim());
    if (name === 'supertrend') expect(labels).toContain('BUY');
    else expect(labels, 'strategy-owned entries cannot use the generic ticket').not.toContain('BUY');
    expect(labels, 'SELL cannot create exposure from a signal').not.toContain('SELL');
  });

  it('offers sizing on every board', () => {
    for (const [name, signals] of Object.entries(boards)) {
      const container = expand(signals);
      expect(container.textContent, `${name} has no sizing`).toMatch(/position|qty|lot/i);
    }
  });
});

describe('each engine states its own provenance', () => {
  // The one slot on the row that is deliberately NOT shared. Three engines,
  // three different answers to "where did this come from" — a badge that said
  // the same thing everywhere would be decoration.
  it('gives every engine a badge in its own vocabulary', () => {
    const labels = Object.entries(boards).map(([name, signals]) => [name, signals[0].origin?.label] as const);
    for (const [name, label] of labels) expect(label, `${name} has no origin`).toBeTruthy();
    const distinct = new Set(labels.map(([, l]) => l));
    expect(distinct.size, 'engines share a badge label').toBe(labels.length);
  });

  it('explains each one in more than a restatement of its label', () => {
    for (const [name, signals] of Object.entries(boards)) {
      const origin = signals[0].origin!;
      expect(origin.hint.length, `${name} hint too short`).toBeGreaterThan(30);
      expect(origin.hint.toLowerCase()).not.toBe(origin.label.toLowerCase());
    }
  });

  it('reads SuperTrend’s as the scan that found it', () => {
    expect(boards.supertrend[0].origin!.label).toBe('SPOT');
  });

  it('reads Gamma Unwind’s as the gate count it cleared', () => {
    expect(boards.gamma_move[0].origin!.label).toBe('ENTRY 4/4');
  });
});

/**
 * The two tables' shared presentation tokens.
 *
 * SuperTrend's table is a bespoke implementation and Adaptive Edge's is the
 * shared `SignalBoard`. They already render against one column spec and one set
 * of geometry — `ROW_METRICS` — but SuperTrend held the numbers as literals, so
 * they agreed by coincidence rather than by construction and could drift on the
 * next edit to either.
 */
describe('ROW_METRICS is the single type and geometry scale', () => {
  it('carries the type scale both tables use', () => {
    // If a value moves here it moves in both tables. That is the point.
    expect(ROW_METRICS.instrumentFontSize).toBe(13);
    expect(ROW_METRICS.cellFontSize).toBe(11);
    expect(ROW_METRICS.parentFontSize).toBe(12);
  });

  it('carries the row geometry both tables use', () => {
    expect(ROW_METRICS.legHeight).toBe(41);
    expect(ROW_METRICS.gap).toBe(16);
    expect(ROW_METRICS.legPadding).toBe('0 16px');
    expect(ROW_METRICS.parentPadding).toBe('10px 12px');
  });
});

/**
 * Release board action ownership.
 *
 * The shared board's side of this is covered next door: `trade` and `chart` are in
 * `BOARD_COLUMNS`, the picker can switch either off independently, and a cell
 * whose engine supplied no handler renders empty rather than crashing.
 *
 * That last property is what makes this test necessary. An engine board that
 * forgets to pass `renderTrade` gets a Trade column that is simply blank — no
 * error, no failing assertion, and a header promising a button that is not there.
 * `useBoardRowActions` builds both from a `BoardSignal` alone, so there is never a
 * reason for a board to omit them; the only way it happens is by being forgotten,
 * which is exactly the case a source-level check catches and a render test on one
 * engine does not.
 */
import { describe, it, expect } from 'vitest';
import snapback from '../SnapbackBoard.tsx?raw';
import gammaMove from '../GammaMoveBoard.tsx?raw';
import superTrendShared from '../../SuperTrendSharedBoard.tsx?raw';
import rowActions from '../useBoardRowActions.tsx?raw';

const BOARDS: Array<[string, string]> = [
  ['Snapback', snapback],
  ['Gamma Unwind', gammaMove],
  ['SuperTrend (shared)', superTrendShared],
];

describe('row actions on every engine board', () => {
  it('keeps execution strategy-owned on Gamma and Snapback, and shared only on SuperTrend', () => {
    expect(superTrendShared).toMatch(/renderTrade=\{isHistoricalSim \? undefined : rowActions\.renderTrade\}/);
    expect(gammaMove).toMatch(/arm\.mutate\(sig\.id\)/);
    expect(snapback).toMatch(/useExecuteSnapbackSignal/);
    expect(snapback).toMatch(/execute\.mutate\(sig\.id\)/);
  });

  it('keeps SuperTrend replay and review rows read-only', () => {
    expect(superTrendShared).toMatch(
      /renderTrade=\{isHistoricalSim \? undefined : rowActions\.renderTrade\}/,
    );
    expect(superTrendShared).toMatch(/isHistoricalSim=\{isHistoricalSim\}/);
  });

  it('passes renderChart, so the signal opens its own chart', () => {
    for (const [name, src] of BOARDS) {
      expect(src, `${name} does not pass renderChart`).toMatch(/renderChart=\{/);
    }
  });

  it('keeps the shared helper buy-only for SuperTrend entry rows', () => {
    expect(rowActions).toMatch(/onBuy=\{order\('BUY'\)\}/);
    expect(rowActions).not.toMatch(/onSell=/);
  });

  it('asks for the shared column list, so neither column can go missing', () => {
    // `columns={...}` — not `requested={...}`, which was a declared-but-dead prop
    // four of these boards passed until it was removed.
    for (const [name, src] of BOARDS) {
      expect(src, `${name} should pass columns=`).toMatch(/columns=\{/);
      expect(src, `${name} still passes the dead requested prop`).not.toMatch(/requested=\{/);
    }
  });
});

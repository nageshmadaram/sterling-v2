/**
 * The numbers an engine position shows, in one place.
 *
 * Every figure is defined once so the positions table, the signal board and the
 * Telegram alerts agree on what they mean:
 *
 * - Prices (`entry`, `ltp`, `stop`) are option PREMIUM per unit, never the
 *   underlying's index level.
 * - `qty` is UNITS (lots x lot size), so rupee figures multiply by it directly and
 *   never by `lot_size` again.
 * - `pnlInr` is the open profit against the average fill, gross of costs.
 * - `riskInr` is what the ENTRY stop was sized to lose; `capitalInr` is the premium
 *   paid.
 */
import type { EngineOpenPosition } from '../../types/kiteEngine';

export interface PositionMetrics {
  /** Average fill, or the intended entry until a fill is confirmed. */
  entry: number | null;
  stop: number | null;
  /** SL while the stop is where it started; TSL once it has trailed. */
  stopKind: 'SL' | 'TSL' | null;
  ltp: number | null;
  pnlInr: number | null;
  pnlPct: number | null;
  riskInr: number | null;
  capitalInr: number | null;
  /** Fill versus the intended entry; POSITIVE is adverse (paid more / sold lower). */
  slippagePct: number | null;
}

const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

export function positionMetrics(
  p: Pick<EngineOpenPosition,
    'fill_price' | 'entry_premium' | 'stop_premium' | 'qty' | 'direction' | 'vehicle'> & {
    initial_stop_premium?: number;
  },
  liveLtp: number | null | undefined,
): PositionMetrics {
  const sign = p.direction === 'short' ? -1 : 1;
  const intended = price(p.entry_premium);
  const entry = price(p.fill_price) ?? intended;
  const stop = price(p.stop_premium);
  const initial = price(p.initial_stop_premium);
  const ltp = price(liveLtp);
  const qty = Number.isFinite(p.qty) && p.qty > 0 ? p.qty : 0;

  let stopKind: PositionMetrics['stopKind'] = null;
  if (stop != null) {
    // A long's stop trails UP, a short future's DOWN; anything else is still the entry stop.
    const trailed = initial != null && (sign > 0 ? stop > initial + 0.005 : stop < initial - 0.005);
    stopKind = trailed ? 'TSL' : 'SL';
  }

  const hasMove = entry != null && ltp != null;
  return {
    entry,
    stop,
    stopKind,
    ltp,
    pnlInr: hasMove && qty > 0 ? (ltp! - entry!) * qty * sign : null,
    pnlPct: hasMove ? ((ltp! - entry!) / entry!) * 100 * sign : null,
    riskInr: entry != null && initial != null && qty > 0 ? Math.abs(entry - initial) * qty : null,
    // Futures are margined, not paid for: the premium figure only means something for options.
    capitalInr: entry != null && qty > 0 && p.vehicle !== 'futures' ? entry * qty : null,
    slippagePct: price(p.fill_price) != null && intended != null
      ? ((p.fill_price - intended) / intended) * 100 * sign
      : null,
  };
}

/**
 * SuperTrend and Navigator rows -> BoardSignal.
 *
 * One adapter for two engines because they share a row type: a Navigator
 * signal is an `EngineSignalRow` with `source === 'navigator'`, produced from
 * its own AVWAP evidence with no SuperTrend trigger at all. They differ in what
 * they can fill, and that difference is honest rather than cosmetic:
 *
 *   SuperTrend  trails, and quotes no target — it is trend-following, so it
 *               exits on the red counter or the trail, never at a fixed level.
 *   Navigator   quotes a stop/target bracket up front and does not run a red
 *               counter.
 *
 * The board drops whichever column is empty, so neither engine implies it
 * forgot to fill in the other's field.
 *
 * A row carries one leg per moneyness. The board shows a signal per leg,
 * because a leg is what actually gets bought — the parent row is a thesis, not
 * an order.
 */
import type { AlignmentChip, EngineSignalRow, OptionLeg } from '../../../types/kiteEngine';
import { computeGreeksFromLeg } from '../../../utils/computeGreeks';
import { k } from '../../../styles/kiteUI';
import type { BoardDayMove, BoardOrigin, BoardSection, BoardSignal, BoardStatus, EngineId } from './boardTypes';
import { barCloseMs, parseTimestampMs, sessionDayKey, normalizeMode, withMode, MODE_LABEL } from './boardTypes';

/**
 * A tradable price, or nothing.
 *
 * A premium of zero is not a level. A feed emitting `0` for "no stop set"
 * renders as a "0.00" stop indistinguishable from a real one, which on a bought
 * option is the difference between a protected position and an unprotected one.
 */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

/**
 * Which scan surfaced this signal.
 *
 * "Both agree" is the one worth distinguishing: in derivatives mode a single
 * contract can produce a spot row AND a premium row with different entries,
 * and without this they read as a duplicate.
 */
/**
 * The badges this engine wants read without opening the row.
 *
 * Each carries a distinction the row is otherwise silent about:
 *
 * - **Which rule closed it.** A trail breach and a red-counter close are not the
 *   same event, and the difference matters most when they disagree: the premium
 *   can be through its trail while the counter has not yet flipped enough lines
 *   to close, and that gap is where an open drawdown builds. This board's own
 *   history has an entry of 971 sitting beside an LTP of 193.
 * - **A re-entry.** The same trend re-arming is not a second independent setup,
 *   and auto-exec's one-position-per-instrument guard will not open another.
 *   Without the badge two rows on one contract look like a duplicate.
 * - **Navigator's verdict**, when it has one, because the two systems can
 *   disagree and the row otherwise shows only SuperTrend's view.
 */
function contractExitReason(row: EngineSignalRow, leg?: OptionLeg): string | null | undefined {
  return leg && row.source === 'derivatives' ? leg.exit_reason : row.exit_reason;
}

function contractExitState(row: EngineSignalRow, leg: OptionLeg): string | null | undefined {
  return row.source === 'derivatives' ? leg.exit_state : leg.exit_state ?? row.exit_state;
}

function marksOf(row: EngineSignalRow, opts: SuperTrendAdapterOptions, leg?: OptionLeg): BoardOrigin[] {
  const marks: BoardOrigin[] = [];

  const reason = row.source === 'derivatives' && !leg ? null : contractExitReason(row, leg);
  if (reason) {
    const trail = reason.startsWith('trail breach');
    const theta = reason.startsWith('time decay');
    marks.push({
      label: trail ? 'TSL exit' : theta ? 'Theta exit' : 'counter exit',
      tone: trail ? 'amber' : theta ? 'amber' : 'dim',
      hint: trail
        ? leg ? closedReasonText(reason, null, price(leg.premium_sl)).hint : `Underlying signal exit: ${reason}. Contract stop levels are shown on each leg.`
        : theta
          ? `Closed by the time-decay limit — ${reason}. Price consolidated without expanding momentum.`
          : `Closed by the red counter — ${reason}.`,
    });
  }

  const firstMs = opts.originalEntryMs?.get(`${row.underlying}|${row.direction}|${row.source ?? 'spot'}`);
  if (firstMs != null && row.timestamp_ms != null && firstMs < row.timestamp_ms) {
    marks.push({
      label: 're-entry',
      tone: 'dim',
      hint: `Same trend re-arming: an earlier entry on ${row.underlying} ${row.direction} is still running. Not a second independent setup — auto-exec's one-position-per-instrument guard will not open another.`,
    });
  }

  // Suppressed under the SuperTrend-only lens, which is defined as this board
  // with no Navigator in it. The row can still HOLD a navigator verdict — the
  // row filter removes rows Navigator originated, not Navigator's opinion of a
  // SuperTrend setup — so the badge has to be gated separately.
  const nav = row.navigator;
  if (nav?.status && opts.signalMode !== 'supertrend') {
    marks.push({
      label: `Nav ${nav.status.replace('_', ' ')}`,
      tone: nav.status === 'CONFIRMED' || nav.status === 'HIGH_CONVICTION' ? 'green' : 'dim',
      hint: `Value-Flow Navigator: ${nav.status}. Reasons: ${nav.reason_codes?.join(', ') || 'none'}.`,
    });
  }
  return marks;
}

function originOf(row: EngineSignalRow): BoardOrigin | undefined {
  switch (row.source) {
    case 'spot':
      return { label: 'SPOT', tone: 'brand', hint: "From the stock or index chart. Options shown to buy." };
    case 'derivatives':
      return { label: 'PREMIUM', tone: 'blue', hint: "Read from this option's own premium chart." };
    case 'confluence':
      return { label: 'BOTH AGREE', tone: 'green', hint: "The underlying fired and this option's own premium confirmed it." };
    case 'navigator':
      return { label: 'NAVIGATOR', tone: 'purple', hint: "Found by Navigator, not SuperTrend." };
    default:
      return undefined;
  }
}

const engineOf = (row: EngineSignalRow): EngineId =>
  row.source === 'navigator' ? 'navigator' : 'supertrend';

/**
 * Where the trade is in its life.
 *
 * `is_active` means the trend held on every bar since entry, and `is_fresh`
 * means it entered on the latest closed bar — so fresh-and-active is the only
 * combination that is a call to action rather than a running position.
 */
function status(row: EngineSignalRow, leg: OptionLeg): BoardStatus {
  if (contractExitReason(row, leg)) return 'ended';
  const active = leg.is_active ?? row.is_active ?? false;
  if (!active) return 'ended';
  const ownFresh = row.source !== 'derivatives'
    || parseTimestampMs(leg.entry_timestamp_ms ?? leg.signal_timestamp_ms) === parseTimestampMs(row.timestamp_ms);
  if (row.is_fresh && ownFresh) return 'armed';
  // The red counter is the exit rule counting against an open position:
  // anything above zero means the thesis is being withdrawn.
  const reds = Number((contractExitState(row, leg) ?? '').split('/')[0]);
  return Number.isFinite(reds) && reds > 0 ? 'weakening' : 'running';
}

/** Trend evidence — what the engine saw, as opposed to what it decided. */
function evidenceSection(row: EngineSignalRow, leg?: OptionLeg): BoardSection {
  return {
    title: 'Trend & volatility',
    layout: 'tiles',
    summary: row.regime,
    stats: [
      { label: 'Spot', value: (row.source === 'derivatives' ? price(row.underlying_spot) : price(row.spot))?.toFixed(2) ?? '—', hint: 'Underlying price at signal time' },
      { label: 'Score', value: row.score?.toFixed(0), hint: 'Engine conviction, 0-100' },
      { label: 'ADX', value: row.adx == null ? undefined : row.adx.toFixed(1), hint: 'Trend strength at signal time' },
      { label: 'ATR pct', value: row.atr_pct == null ? undefined : `${row.atr_pct.toFixed(0)}%`, hint: 'Volatility rank against recent history' },
      { label: 'Alignment', value: row.source === 'derivatives' ? (leg?.alignment ? alignmentText(leg.alignment) : '—') : alignmentText(row.alignment), hint: 'Fast / mid / slow SuperTrend, in that order' },
      { label: 'Regime', value: row.regime },
      { label: 'Source', value: row.source ?? undefined, hint: 'Which scan surfaced this — spot, derivatives, confluence or Navigator' },
      ...(leg ? [{ label: 'Moneyness (strike steps)', value: leg.moneyness,
        hint: 'ITM/OTM is the engine strike-step label at signal time, not a delta classification.' }] : []),
      ...(leg?.strike != null && Number.isFinite(leg.strike) && price(row.spot) != null
        ? [{ label: 'Strike − signal spot', value: `${leg.strike > row.spot ? '+' : ''}${(leg.strike - row.spot).toFixed(2)} pts`,
          hint: `Strike ${leg.strike.toFixed(2)} minus underlying ${row.spot.toFixed(2)} at this row's signal time. Delta is a separate model estimate from available quote/spot inputs.` }] : []),
    ],
  };
}

/** The three lines as arrows, in fast/mid/slow order. */
function alignmentText(chip: AlignmentChip | null | undefined): string | undefined {
  if (!chip) return undefined;
  const arrow = (v: number) => (v > 0 ? '▲' : v < 0 ? '▼' : '·');
  return `${arrow(chip.fast)}${arrow(chip.mid)}${arrow(chip.slow)}`;
}

/**
 * How the position ends.
 *
 * The red counter and the trailing stop are independent rules and either can
 * close a trade, so the counter alone cannot explain an ended row — which is
 * why `exit_reason` is shown beside it rather than instead of it.
 */
function exitSection(row: EngineSignalRow, leg: OptionLeg): BoardSection | null {
  const exitState = contractExitState(row, leg);
  const reason = contractExitReason(row, leg);
  if (!exitState && !reason && !row.exit_reason && row.target == null) return null;
  return {
    title: 'Exit rule',
    layout: 'rows',
    summary: exitState ?? undefined,
    stats: [
      { label: 'Red counter', value: exitState ?? '—', hint: 'SuperTrend lines turned against the position, out of the threshold' },
      { label: 'Why it ended', value: reason ? closedReasonText(reason, null, price(leg.premium_sl)).text : '—', hint: reason ? closedReasonText(reason, null, price(leg.premium_sl)).hint : 'The trail and the counter are separate rules; either can close a trade' },
      { label: row.source === 'derivatives' ? 'Group exit reason' : 'Underlying exit reason', value: row.exit_reason ?? '—', hint: 'Raw engine diagnostic for the underlying signal; a quoted threshold may belong to another contract.' },
      { label: 'Underlying target', value: row.target == null ? '—' : row.target.toFixed(2), hint: 'Navigator quotes one; SuperTrend does not' },
      { label: 'Resolution', value: leg.resolution_note ?? row.resolution_reason ?? '—' },
    ],
  };
}

/** Navigator's own decision record, when it produced or annotated the row. */
function navigatorSection(row: EngineSignalRow): BoardSection | null {
  const nav = row.navigator;
  if (!nav) return null;
  return {
    title: 'Navigator decision',
    layout: 'rows',
    summary: nav.status,
    stats: [
      { label: 'Status', value: nav.status },
      {
        label: 'Eligible to execute',
        value: nav.execution_eligible ? 'yes' : 'no',
        color: nav.execution_eligible ? undefined : k.amber,
        hint: 'Navigator can advise without being cleared to trade',
      },
      { label: 'Effective score', value: nav.effective_score?.toFixed(0) ?? '—', hint: 'Base blended with the suite, where the suite ran' },
      { label: 'Base score', value: nav.base_score?.toFixed(0) ?? '—' },
      { label: 'Data quality', value: nav.data_quality, hint: 'Degraded inputs downgrade a decision rather than hiding it' },
      { label: 'Trigger', value: nav.trigger },
      {
        label: 'Reasons',
        value: nav.reason_codes?.length ? nav.reason_codes.join(', ') : '—',
        hint: 'Why Navigator landed on this status',
      },
    ],
  };
}

/**
 * The moment a SuperTrend signal fired: its candle's CLOSE, not its open.
 * A Navigator row has no candle behind it and keeps the stamp it was given.
 */
function signalMs(row: EngineSignalRow, openMs: number | null): number | null {
  if (openMs == null || row.source === 'navigator') return openMs;
  const minutes = row.signal_timeframe_minutes;
  const barMs = typeof minutes === 'number' && Number.isFinite(minutes) && minutes > 0
    ? minutes * 60_000 : 3_600_000;
  return row.is_replay ? openMs : barCloseMs(openMs, barMs);
}

/**
 * Why a SuperTrend trade closed, in plain words. Two rules close it, whichever comes first:
 * the red counter (with "one red" the first line turning red is enough, so "1/3 red" IS the
 * exit) and the trailing stop (the premium touched the trail, so a row can read "0/3 red").
 */
export function closedReasonText(
  exitReason: string | null | undefined, ltp: number | null | undefined, trail: number | null | undefined,
): { text: string; hint: string } {
  const r = (exitReason || '').toLowerCase();
  if (r.startsWith('trail breach')) {
    // The shared spot/confluence reason or contract-local derivative reason
    // may name a different unit/level. Only show this leg's known premium trail.
    const level = trail != null && Number.isFinite(trail) && trail > 0 ? trail.toFixed(2) : null;
    return { text: `Stop hit${level ? ' ' + level : ''}`, hint: `Closed by the trailing stop: the price fell to ${level || 'the stop'}. The red counter had not fired; whichever rule comes first closes the trade.` };
  }
  if (r.startsWith('red count')) {
    return { text: 'Trend turned red', hint: `Closed by the red counter: ${exitReason}. Enough SuperTrend lines turned red for the exit rule set in SuperTrend settings.` };
  }
  if (r.startsWith('time decay')) return { text: 'Stalled', hint: 'Closed because the price stopped moving for too long (option value was decaying).' };
  if (r.startsWith('time stop')) return { text: 'Time limit', hint: 'Closed at the time limit for this mode.' };
  if (ltp != null && trail != null && trail > 0 && ltp <= trail) {
    return { text: `Stop hit ${trail.toFixed(2)}`, hint: `Closed: the live price ${ltp.toFixed(2)} is at or below the trailing stop ${trail.toFixed(2)}.` };
  }
  return { text: 'Closed', hint: 'The trend ended. Kept for your record; you cannot trade it.' };
}

/**
 * One buyable plan per contract per day. When a stock signals again, the newer signal's leg
 * for the same contract is the plan to follow; the older leg stays visible (it may be a
 * position someone holds) but is marked replaced and cannot be bought, so a trader never
 * sees two buyable plans with different stops for one option.
 * Returns the set of `${timestamp_ms}|${option_symbol}` legs that a newer plan replaced.
 */
export function supersededLegKeys(rows: readonly { timestamp_ms: number; horizon_mode?: string; legs?: { option_symbol?: string }[] }[]): Set<string> {
  const newest = new Map<string, number>();
  for (const row of rows) for (const leg of row.legs ?? []) {
    if (!leg.option_symbol) continue;
    const key = `${sessionDayKey(row.timestamp_ms)}|${row.horizon_mode ?? 'swing'}|${leg.option_symbol}`;
    newest.set(key, Math.max(newest.get(key) ?? 0, row.timestamp_ms ?? 0));
  }
  const out = new Set<string>();
  for (const row of rows) for (const leg of row.legs ?? []) {
    if (!leg.option_symbol) continue;
    const key = `${sessionDayKey(row.timestamp_ms)}|${row.horizon_mode ?? 'swing'}|${leg.option_symbol}`;
    if ((row.timestamp_ms ?? 0) < (newest.get(key) ?? 0)) out.add(`${row.timestamp_ms}|${leg.option_symbol}`);
  }
  return out;
}

export function supertrendLegToBoard(
  row: EngineSignalRow,
  leg: OptionLeg,
  index: number,
  opts: SuperTrendAdapterOptions = {},
): BoardSignal {
  if (row.is_replay) opts = { ...opts, quotes: undefined, spotOf: undefined };
  const engine = engineOf(row);
  const localOpenMs = parseTimestampMs(leg.entry_timestamp_ms ?? leg.signal_timestamp_ms);
  const openMs = row.source === 'derivatives' ? localOpenMs : parseTimestampMs(
    leg.entry_timestamp_ms ?? leg.signal_timestamp_ms ?? row.timestamp_ms ?? (row as any).timestamp ?? (row as any).created_at ?? (row as any).entered_at ?? (row as any).session_date
  );
  const atMs = signalMs(row, openMs);
  const sections = [evidenceSection(row, leg), exitSection(row, leg), navigatorSection(row)]
    .filter(Boolean) as BoardSection[];
  const quantity = leg.lot_size ?? null;
  const quoteKey = `${row.exchange}:${leg.option_symbol}`;
  const dayMove = dayMoveFromQuote(opts.quotes?.[quoteKey], opts.chgBasis);

  return {
    id: [
      engine, row.source ?? 'spot', row.horizon_mode ?? 'swing',
      row.signal_timeframe_minutes ?? 60, row.exchange, row.underlying,
      row.timestamp_ms, leg.option_symbol, index,
    ].join('-'),
    engine,
    historical: row.is_replay === true,
    underlying: row.underlying,
    instrument: {
      symbol: leg.option_symbol,
      exchange: row.exchange,
      kind: 'option',
      optionType: (leg.option_type as 'CE' | 'PE') ?? row.option_type,
      strike: leg.strike ?? null,
      expiry: leg.expiry ?? null,
      lotSize: leg.lot_size ?? null,
      moneyness: leg.moneyness ?? null,
      quoteKey: leg.option_symbol ? `${row.exchange}:${leg.option_symbol}` : null,
    },
    direction: row.direction,
    status: status(row, leg),
    atMs,
    levels: {
      // The LIVE premium, and nothing else. Falling back to the entry made the two
      // identical by construction: a position far under water printed an LTP equal
      // to its entry, the entry bracket read (+0.00) and TSL HIT could never fire --
      // the very check that says an open drawdown is building. With no quote the
      // honest value is blank.
      ltp: price(
        (opts.quotes?.[quoteKey]?.last_price as number | undefined)
        ?? (leg as any).last_price
        ?? (leg as any).ltp,
      ),
      entry: price(leg.premium_spot),
      // The hard stop set at entry and the ratchet are different numbers, and
      // the board shows both: one says what was risked, the other what is left.
      stop: price(leg.entry_sl),
      trail: price(leg.premium_sl),
      // SuperTrend has no fixed premium target. Never synthesize a 2R target:
      // that changes the strategy contract and makes the shared table claim an
      // exit objective the engine does not use. Navigator-originated rows may
      // supply their own explicit premium target.
      target: price(leg.premium_target),
      // The live signal payload does not carry a broker-confirmed realized exit
      // premium. premium_sl is the TRAILING STOP, not an exit fill. Show "—"
      // until an actual exit-price field is available rather than fabricating
      // a realized fill from the stop level.
      exit: null,
    },
    // On the LEG, not the parent. The parent stands for the idea and its
    // instrument is the underlying; giving it a contract's day move would label
    // NIFTY's move with a strike's.
    dayMove,
    sizing: {
      lots: null,
      quantity,
      // Premium risked per lot, if both ends of the stop are known.
      atRiskInr: price(leg.premium_spot) != null && price(leg.entry_sl) != null && leg.lot_size
        ? Math.max(0, (price(leg.premium_spot)! - price(leg.entry_sl)!) * leg.lot_size)
        : null,
      deployedInr: price(leg.premium_spot) != null && leg.lot_size ? price(leg.premium_spot)! * leg.lot_size : null,
    },
    score: row.score ?? null,
    executionEligible: (
      row.is_replay !== true
      && row.view_only !== true
      && (row.source !== 'derivatives' || parseTimestampMs(leg.entry_timestamp_ms ?? leg.signal_timestamp_ms) != null)
      && !priceAtOrBelowStop(row, leg, opts)
      // Buyable only with a stop below the entry: that stop is armed at Zerodha after the fill.
      && price(leg.entry_sl ?? leg.premium_sl) != null && price(leg.premium_spot) != null
      && price(leg.entry_sl ?? leg.premium_sl)! < price(leg.premium_spot)!
      && (leg.is_active ?? row.is_active ?? false) === true
      && (opts.nowMs == null || sessionDayKey(atMs) === sessionDayKey(opts.nowMs))
    ),
    origin: originOf(row),
    marks: marksOf(row, opts, leg),
    // The red counter, e.g. "0/3 red", while the trade runs; once closed, WHY it closed.
    exitProgress: status(row, leg) === 'ended'
      ? closedReasonText(contractExitReason(row, leg), null, price(leg.premium_sl)).text
      : (contractExitState(row, leg) || null),
    flags: flagsFor(row, leg, opts),
    // Solved from the live quote, not replayed from the scan. Omitted rather
    // than guessed when the implied volatility has no solution — a delta the
    // model could not find is not a delta.
    delta: (() => {
      const spot = opts.spotOf?.(row.underlying);
      if (!spot || leg.strike == null || !leg.expiry) return null;
      const q = opts.quotes?.[`${row.exchange}:${leg.option_symbol}`];
      const g = computeGreeksFromLeg(leg.strike, leg.expiry, leg.option_type, spot, q, leg.lot_size ?? null);
      return g && Number.isFinite(g.iv) && g.iv > 0 ? g.delta : null;
    })(),
    reason: row.source === 'derivatives'
      ? leg.exit_reason ?? (localOpenMs == null
        ? 'Contract signal time is unavailable. Wait for a fresh scan before entry.'
        : leg.resolution_note ?? null)
      : row.exit_reason ?? row.resolution_reason ?? null,
    sections,
  };
}

/**
 * The signal itself: the idea, before it is expressed through any contract.
 *
 * Its price columns stay empty on purpose. The thesis has no premium, and
 * lifting one leg's numbers up to stand for the rest would be a lie about
 * which strike you would actually trade. What the parent does carry is what
 * belongs to the idea and to no single leg — the underlying, the scan that
 * found it, the trend evidence.
 */
function supertrendSignalToBoard(
  row: EngineSignalRow,
  legs: BoardSignal[],
  index: number,
  opts: SuperTrendAdapterOptions = {},
): BoardSignal {
  const engine = engineOf(row);
  // The group is as live as its liveliest leg: a signal with one running
  // contract is running, even if four others have closed.
  const status = legs.reduce<BoardStatus>(
    (best, leg) => (STATUS_ORDER.indexOf(leg.status) < STATUS_ORDER.indexOf(best) ? leg.status : best),
    legs[0]?.status ?? 'watching',
  );

  return {
    id: `${engine}-${row.underlying}-${row.timestamp_ms}-${index}`,
    engine,
    historical: row.is_replay === true,
    underlying: row.underlying,
    instrument: {
      // The underlying, not a contract — that is what the row is about.
      symbol: row.underlying,
      exchange: row.exchange,
      kind: 'index',
      strike: null,
      expiry: null,
      lotSize: null,
      quoteKey: null,
    },
    direction: row.direction,
    status,
    atMs: legs[0]?.atMs != null && parseTimestampMs(row.timestamp_ms) == null
      ? legs[0].atMs
      : signalMs(row, parseTimestampMs(
        row.timestamp_ms ?? (row as any).timestamp ?? (row as any).created_at ?? (row as any).entered_at ?? (row as any).session_date,
      )),
    // The live underlying where we have it; the scan's spot otherwise.
    underlyingPrice: (row.is_replay ? null : opts.spotOf?.(row.underlying))
      ?? (row.source === 'derivatives' ? price(row.underlying_spot) : price(row.spot)),
    levels: { ltp: null, entry: null, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: row.score ?? null,
    origin: originOf(row),
    marks: marksOf(row, opts),
    // The red counter, e.g. "0/3 red". A count, not a price.
    exitProgress: row.source === 'derivatives' ? 'Per contract' : row.exit_state || null,
    reason: row.source === 'derivatives'
      ? (status === 'ended' ? 'Closed contracts; see each contract for its exit rule.' : null)
      : row.exit_reason ?? row.resolution_reason ?? null,
    sections: [evidenceSection(row), navigatorSection(row)].filter(Boolean) as BoardSection[],
    children: legs,
  };
}

/**
 * Why a trade ended, and how close it is to ending.
 *
 * The trailing stop and the red counter are independent rules and either can
 * close a position, so the badge names which one did — "counter exit" and
 * "TSL exit" are different failures and reading one as the other sends you
 * looking at the wrong rule.
 */
export function legSafetyTags(row: EngineSignalRow, leg: any, ended: boolean, nowMs = Date.now()): { label: string; hint: string }[] {
  const tags: { label: string; hint: string }[] = [];
  const mode = normalizeMode(row.horizon_mode);
  if (row.view_only) tags.push({ label: `VIEW ONLY · ${mode ? MODE_LABEL[mode] : row.horizon_mode}`, hint: `Signal from SuperTrend's ${mode ? MODE_LABEL[mode] : ''} mode, which it is not set to trade. Switch SuperTrend to that mode in Settings to trade it.` });
  if (ended) return tags;
  if (leg.replaced) tags.push({ label: 'REPLACED', hint: 'A newer signal on this stock has its own plan for this contract. Follow the newer one.' });
  const stop = leg.entry_sl ?? leg.premium_sl;
  if (stop == null || !(stop > 0)) tags.push({ label: 'NO STOP', hint: 'This leg has no stop, so it cannot be bought.' });
  else if (leg.premium_spot != null && stop >= leg.premium_spot) tags.push({ label: 'STOP ≥ ENTRY', hint: `The stop (${Number(stop).toFixed(2)}) is at or above the entry (${Number(leg.premium_spot).toFixed(2)}); buying would sell at once.` });
  const holdDays = row.horizon_mode === 'swing' ? 5 : row.horizon_mode === 'overnight' ? 2 : 0;
  if (holdDays && leg.expiry) {
    const days = Math.ceil((Date.parse(`${leg.expiry}T15:30:00+05:30`) - nowMs) / 86_400_000);
    if (Number.isFinite(days) && days >= 0 && days <= holdDays) tags.push({ label: 'EXPIRES SOON', hint: `Expires in ${days} day${days === 1 ? '' : 's'}, before this mode's planned hold ends; it would be closed the day before expiry. Prefer the next expiry.` });
  }
  return tags;
}

function priceAtOrBelowStop(row: EngineSignalRow, leg: OptionLeg, opts: SuperTrendAdapterOptions): boolean {
  if (row.is_replay) return false;
  const live = price(opts.quotes?.[`${row.exchange}:${leg.option_symbol}`]?.last_price ?? (leg as any).last_price ?? (leg as any).ltp);
  const stop = price(leg.entry_sl ?? leg.premium_sl);
  return live != null && stop != null && live <= stop;
}

function flagsFor(row: EngineSignalRow, leg: OptionLeg, opts: SuperTrendAdapterOptions): BoardOrigin[] {
  const flags: BoardOrigin[] = legSafetyTags(row, leg, status(row, leg) === 'ended', opts.nowMs)
    .map(tag => ({ ...tag, tone: tag.label.startsWith('VIEW ONLY') ? 'dim' : 'amber' }));
  if (status(row, leg) !== 'ended' && priceAtOrBelowStop(row, leg, opts)) flags.push({
    label: 'PRICE ≤ STOP', tone: 'amber', hint: 'The known premium is at or below this plan’s hard stop. Refresh the plan before a new entry; server admission remains authoritative.',
  });

  const reason = contractExitReason(row, leg);
  if (reason?.startsWith('trail breach')) {
    flags.push({ label: 'TSL exit', tone: 'amber', hint: closedReasonText(reason, null, price(leg.premium_sl)).hint });
  } else if (reason?.startsWith('time decay')) {
    flags.push({ label: 'Theta exit', tone: 'amber', hint: `Closed on the time-decay limit — ${reason}. Price consolidated without expanding, so the trade closed rather than bleed theta.` });
  } else if (reason) {
    flags.push({ label: 'counter exit', tone: 'dim', hint: `Closed by the red counter — ${reason}.` });
  }

  const counter = contractExitState(row, leg);
  if (counter && !reason) {
    flags.push({ label: counter, tone: 'dim', hint: 'Red lines now, out of how many close the trade.' });
  }

  // A second entry on an instrument whose earlier trade is still running is a
  // re-entry, not a fresh setup — worth saying, because sizing it as new
  // doubles the exposure on one idea.
  const firstEntry = opts.originalEntryMs?.get(`${row.underlying}|${row.direction}|${row.source ?? 'spot'}`);
  if (firstEntry != null && row.timestamp_ms > firstEntry) {
    flags.push({ label: 're-entry', tone: 'purple', hint: 'Adds to a trade already open on this name.' });
  }

  return flags;
}

/** What the adapter needs beyond the rows themselves. */
export interface SuperTrendAdapterOptions {
  /** SuperTrend's configured mode (one per account), shown on every row. */
  mode?: string | null;
  /** Live quotes by `EXCHANGE:SYMBOL`, for the per-leg delta. */
  quotes?: Record<string, Record<string, unknown> | undefined>;
  /** First still-running entry per `underlying|direction|source`. */
  originalEntryMs?: Map<string, number>;
  /** The underlying's live price, which the Greeks need. */
  spotOf?: (underlying: string) => number | null;
  /**
   * Whether today's move is measured from the previous close or today's open.
   * The operator's choice; the board only reports what it is told.
   */
  chgBasis?: 'close' | 'open';
  /** Board clock used to prevent retained prior-session rows from executing. */
  nowMs?: number;
  /**
   * Which lens the board is showing.
   *
   * Needed only to suppress the Navigator badge under `'supertrend'`. That lens
   * means "what this board looks like with no Navigator at all", so a Navigator
   * verdict has no business on the row — and filtering the ROWS is not enough,
   * because a SuperTrend setup can carry Navigator's opinion of it without
   * having come from Navigator.
   */
  signalMode?: 'supertrend' | 'navigator' | 'combined' | 'common';
}

/**
 * Today's move on one contract.
 *
 * Two rules carried over from the table this replaces, both of which were bugs
 * once:
 *
 * - `net_change` is in RUPEES. An option premium has no previous close on the
 *   day it starts trading, which is exactly when that branch runs, so there is
 *   nothing to divide by — the percentage stays null rather than being derived
 *   from the last price, which printed a 12-rupee move on a 90-rupee premium as
 *   "12.00%".
 * - A zero or missing base is not a base. Dividing by it yields Infinity, and a
 *   board that prints "Infinity%" beside a live position is worse than one that
 *   prints nothing.
 */
export function dayMoveFromQuote(
  quote: Record<string, unknown> | undefined,
  basis: 'close' | 'open' = 'close',
): BoardDayMove | null {
  if (!quote) return null;
  const last = typeof quote.last_price === 'number' ? quote.last_price : null;
  const ohlc = quote.ohlc as { close?: number; open?: number } | undefined;
  const base = basis === 'close' ? ohlc?.close : ohlc?.open;

  if (last != null && typeof base === 'number' && base > 0) {
    const abs = last - base;
    return { abs, pct: (abs / base) * 100 };
  }
  const net = quote.net_change;
  if (typeof net === 'number') return { abs: net, pct: null };
  return null;
}

/** Most-actionable first, so a group takes its liveliest leg's status. */
const STATUS_ORDER: readonly BoardStatus[] = ['armed', 'running', 'weakening', 'watching', 'ended', 'error'];

/**
 * One board row per signal, with its contracts nested underneath.
 *
 * SuperTrend produces around fifty signals carrying nearly three hundred legs
 * — NIFTY alone can be thirty-seven strikes. Flattened, that is a board nobody
 * can read; grouped, it is fifty ideas you can open.
 */
export function supertrendToBoard(
  rows: readonly EngineSignalRow[],
  opts: SuperTrendAdapterOptions = {},
): BoardSignal[] {
  const replaced = supersededLegKeys(rows);
  return rows.map((row, i) => {
    const legs = (row.legs ?? []).map((leg, j) => {
      const board = supertrendLegToBoard(row, leg, j, opts);
      if (!replaced.has(`${row.timestamp_ms}|${leg.option_symbol}`)) return board;
      return { ...board, executionEligible: false,
        flags: [...(board.flags ?? []).filter(flag => flag.label !== 'REPLACED'), { label: 'REPLACED', tone: 'dim' as const, hint: 'A newer signal on this stock has its own plan for this contract. Follow the newer one.' }] };
    });
    // Each row carries its own mode: other modes' rows are on the board too (view-only).
    const board = withMode(supertrendSignalToBoard(row, legs, i, opts), normalizeMode(row.horizon_mode ?? opts.mode));
    return row.view_only ? { ...board, viewOnly: true, children: board.children?.map((c) => ({ ...c, viewOnly: true })) } : board;
  });
}

/**
 * Snapback snapshot -> BoardSignal.
 *
 * Two things this engine puts on the board that no other one has, and both are
 * on the row rather than behind a click because both change whether you would
 * take the trade:
 *
 * **How stretched it is.** The signal is a distance from a mean, so the size of
 * that distance IS the conviction. A 3-ATR push and a 1.5-ATR one are the same
 * rule at very different strengths.
 *
 * **Whether the premium is a price or an assumption.** With the market shut, or
 * before any contract resolves, the only premium available is a Black-Scholes
 * one computed at a modelled vol. Every other engine here would show that as a
 * number; this one marks it, because an operator cannot size a trade off a
 * figure whose provenance is ambiguous, and this repo has shipped exactly that
 * bug before.
 */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import type { SnapbackPosition, SnapbackRow, SnapbackSide } from '../../../hooks/useSnapback';
import { LABELS, fmtMoney, fmtPct, fmtPnl, fmtPx, fmtQty } from '../../../lib/format';
import { parseInstrument } from '../../../lib/instrument';

/** A tradable price, or nothing. Zero is not a level. */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

// One number format with every other board: Indian grouping, fixed decimals, an em
// dash for unknown (see lib/format).
const n = (v: number | null | undefined, dp = 2): string => fmtPx(v, dp);

// All non-swing modes use the completed-minute-candle signal/plan presentation.
// Execution eligibility comes exclusively from the server's row decision.
const isMinuteBarMode = (row: SnapbackRow) =>
  ['ultra_scalping', 'scalp', 'intraday', 'overnight'].includes(row.trading_mode);
const metric = (row: SnapbackRow, key: string): number | null => {
  const value = row.metrics?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

const SIDE_LABEL: Record<SnapbackSide, string> = {
  fade_up: 'Fade push',
  fade_down: 'Fade flush',
};

const SIDE_TONE: Record<SnapbackSide, BoardOrigin['tone']> = {
  fade_up: 'purple',
  fade_down: 'amber',
};

function instrument(row: SnapbackRow): BoardInstrument {
  const c = row.contract;
  if (!c) {
    // No listed contract resolved. Chart the underlying rather than mint an
    // NFO symbol that does not exist.
    return {
      symbol: row.symbol, exchange: 'NSE', kind: 'equity',
      moneyness: null, quoteKey: row.symbol ? `NSE:${row.symbol}` : null,
    };
  }
  return {
    symbol: c.symbol,
    exchange: c.exchange || 'NFO',
    kind: 'option',
    optionType: c.option_type,
    strike: c.strike,
    expiry: c.expiry,
    lotSize: c.lot_size,
    moneyness: c.moneyness ?? null,
    quoteKey: c.symbol ? `${c.exchange || 'NFO'}:${c.symbol}` : null,
  };
}

function origin(row: SnapbackRow): BoardOrigin {
  return {
    label: SIDE_LABEL[row.side] ?? row.side,
    hint: isMinuteBarMode(row)
      ? 'A completed candle reversed a Bollinger stretch across EMA9, with ADX below the entry cutoff.'
      : row.side === 'fade_up'
      ? `Closed above its ${row.metrics?.lookback_days ?? 20}-session high while `
        + 'stretched above its own mean — buys the put.'
      : `Closed below its ${row.metrics?.lookback_days ?? 20}-session low while `
        + 'stretched below its own mean — buys the call.',
    tone: SIDE_TONE[row.side] ?? 'dim',
  };
}

/**
 * What else is worth seeing WITHOUT opening the row.
 *
 * The stretch is first because it is the conviction. The modelled-premium mark
 * is second because it changes what the price column means.
 */
function flags(row: SnapbackRow): BoardOrigin[] {
  const o = row.outcome;
  const intraday = isMinuteBarMode(row);
  const mode = (row.trading_mode || 'swing').toLowerCase();
  const modeLabel = ({
    ultra_scalping: 'ULTRA', scalp: 'SCALP', intraday: 'INTRADAY',
    overnight: 'OVERNIGHT', swing: 'SWING',
  } as Record<string, string>)[mode] ?? 'SWING';
  const modeTone: BoardOrigin['tone'] = mode === 'scalp' || mode === 'ultra_scalping'
    ? 'amber' : mode === 'intraday' ? 'purple' : 'blue';
  const modeHint = mode === 'ultra_scalping'
    ? 'Ultra scalping — closed 1m reversal signals, bounded by a 20-minute holding deadline.'
    : mode === 'scalp'
      ? 'Scalp mode — fast option mean reversion with a premium stop and trailing management.'
      : mode === 'intraday'
        ? 'Intraday mode — completed 5/15/30m reversal candles and a same-session deadline.'
        : mode === 'overnight'
          ? 'Overnight mode — completed 30/60m reversal candles, broker protection and no weekend-gap carry.'
          : 'Swing mode — multi-day holding period tracking stretch from the session mean.';

  const out: BoardOrigin[] = [
    {
      label: `${row.stretch >= 0 ? '+' : ''}${row.stretch.toFixed(1)} ATR`,
      hint: intraday
        ? 'Prior candle stretch from its intraday Bollinger mean, measured in ATR(14).'
        : `Distance from the ${row.metrics?.mean_ema ?? 20}-session mean, in `
          + 'ATR(14). This is the size of the move being faded.',
      tone: Math.abs(row.stretch) >= 3 ? 'green' : 'dim',
    },
    {
      label: modeLabel,
      hint: modeHint,
      tone: modeTone,
    },
  ];
  if (row.premium_is_modelled) {
    out.push({
      label: 'modelled',
      hint: `No live quote, so the premium is Black-Scholes at `
        + `${row.assumed_iv_pct.toFixed(1)}% vol — the instrument's own `
        + `${row.realized_vol_pct.toFixed(1)}% realised vol times `
        + `${row.assumed_vrp.toFixed(2)}. It is an assumption, not a price.`,
      tone: 'amber',
    });
  }
  if (row.runner_premium != null) {
    out.push({
      label: `runs above \u20b9${row.runner_premium.toFixed(0)}`,
      hint: intraday
        ? 'Continuation threshold. The entry and exit manager checks completed option candles, targets and broker stop updates before treating a runner as active.'
        : 'Above this premium the position is held PAST the horizon, under a '
        + 'give-back ratchet, instead of being closed on the session count. '
        + 'The edge here is a right tail — the top 1% of trades carry 148% of '
        + 'the P&L — so a fixed horizon closes the few trades that pay for the '
        + 'rest. Out of sample this moved the book from +3.46% to +4.03% per '
        + 'entry day on the same 409 entry days.',
      tone: 'blue',
    });
  }
  if (row.contract?.delta != null) {
    out.push({
      label: `Δ ${Math.abs(row.contract.delta).toFixed(2)}`,
      hint: 'Delta is the leverage. This engine picks the strike BY delta '
        + 'rather than by a moneyness rung, because a rung is a different '
        + 'amount of leverage at every vol level and tenor.',
      tone: 'dim',
    });
  }
  if (o) {
    out.push({
      label: fmtPct(o.return_pct, 1, true),
      hint: o.open
        ? `Open ${o.held_days} of ${o.held_days + o.sessions_left} sessions — `
          + 'unrealised, at the replay\u2019s own valuation.'
        : `Closed ${o.reason.replace(/_/g, ' ')} after ${o.held_days} sessions.`,
      tone: o.return_pct >= 0 ? 'green' : 'amber',
    });
  }
  if (o?.beta != null) {
    out.push({
      label: `β ${o.beta.toFixed(2)} hedged`,
      hint: 'The market was hedged out with an index future, sized by this '
        + 'beta. The signal\u2019s edge is RELATIVE — a bought put is a large '
        + 'SHORT position in the market, and over nine rising years that cost '
        + 'more than the edge earned. A future rather than a bought index call: '
        + 'a call is convex and would quietly become a second long-market bet.',
      tone: 'blue',
    });
  }
  if (o?.open) {
    out.push({
      label: `${o.sessions_left}d left`,
      hint: 'Sessions remaining in the holding period. The measured edge is a '
        + 'MEAN over the whole horizon, so an early exit collects a different '
        + 'distribution.',
      tone: 'dim',
    });
  }
  if (row.historical) {
    // Never let a replayed row read as a live one. Same rules, same bars,
    // nobody traded it.
    out.push({
      label: 'replay',
      hint: 'Replayed from stored daily bars — the same rule on the same '
        + 'sessions, but this was not traded. The live scan only looks at the '
        + 'last few closed sessions, so without this the board is blank almost '
        + 'always.',
      tone: 'dim',
    });
  }
  if (row.min_outlay_inr != null) {
    // The number that decides whether a row is actionable at all. A monthly
    // in-the-money option on this universe is ₹30,000 to ₹200,000 of premium
    // per lot, and no percentage budget on a small account reaches it.
    out.push({
      label: `${fmtMoney(row.min_outlay_inr, 0)}/lot`,
      hint: 'Premium for ONE lot — the minimum this trade can be taken in. '
        + 'A bought option cannot be sized below one lot, so this is the real '
        + 'capital requirement.',
      tone: 'dim',
    });
  }
  if (row.contract?.dte != null) {
    out.push({
      label: `${row.contract.dte}d`,
      hint: 'Days to expiry. Theta per rupee of premium runs at about 1/(2T), '
        + 'so a shorter contract spends the edge on decay.',
      tone: 'dim',
    });
  }
  if (intraday) {
    const timeframe = row.timeframe_minutes ?? metric(row, 'timeframe_minutes');
    out.push({
      label: row.max_hold_bars && timeframe ? `hold ≤${row.max_hold_bars * timeframe}m` : 'same session',
      hint: `${row.max_hold_bars ?? '—'} initial bars; runner maximum ${row.runner_max_bars ?? '—'} bars in total. ${mode === 'overnight' ? 'Frozen overnight and no-gap-carry deadlines' : 'Same-session square-off and mode deadlines'} take precedence.`,
      tone: 'dim',
    });
  } else {
    out.push({
      label: `hold ${row.hold_days}d`,
      hint: 'Sessions held. The measured edge is a MEAN over this horizon, so an '
        + 'early exit collects a different distribution from the one measured.',
      tone: 'dim',
    });
  }
  return out;
}

function sections(row: SnapbackRow): BoardSection[] {
  const out: BoardSection[] = [];
  const intraday = isMinuteBarMode(row);
  out.push({
    title: 'Why it fired',
    layout: 'rows',
    summary: `${SIDE_LABEL[row.side]} — back towards ${n(row.mean_target)}`,
    stats: row.reasons.map((r, i) => ({ label: `${i + 1}`, value: r })),
  });
  out.push({
    title: 'The move being faded',
    layout: 'tiles',
    stats: [
      { label: 'Spot', value: n(row.spot) },
      { label: 'Level broken', value: n(row.level) },
      { label: 'Mean target', value: n(row.mean_target) },
      { label: 'Distance', value: fmtPct(row.distance_pct) },
      { label: 'Stretch', value: `${row.stretch.toFixed(2)} ATR` },
      { label: 'Strength', value: row.strength },
    ],
  });
  out.push({
    title: 'Vol and what the premium assumes',
    layout: 'tiles',
    summary: intraday
      ? 'Premium plans require observed option quotes. The intraday realised-vol proxy is not quoted implied volatility; daily swing measurements do not validate this plan.'
      : 'The exit is valued at the ENTRY’s vol in every measurement '
      + 'behind this engine, so the vega a successful fade would earn is '
      + 'credited at exactly zero.',
    stats: [
      ...(intraday
        ? [{ label: 'Intraday vol proxy', value: `${n(row.realized_vol_pct, 1)}%` }]
        : [
          { label: 'Realised vol', value: `${n(row.realized_vol_pct, 1)}%` },
          { label: 'Modelled IV', value: `${n(row.assumed_iv_pct, 1)}%` },
          { label: 'Assumed VRP', value: `${row.assumed_vrp.toFixed(2)}x` },
        ]),
      {
        label: LABELS.entry,
        value: row.premium == null ? '—'
          : `${n(row.premium)}${row.premium_is_modelled ? ' (modelled)' : ''}`,
      },
      { label: 'Ask (quoted)', value: n(row.quote?.premium) },
      ...(intraday ? [] : [{
        label: 'Target (modelled)',
        value: row.target_premium == null ? '—' : n(row.target_premium),
      }]),
      {
        label: 'One lot',
        value: fmtMoney(row.min_outlay_inr),
      },
      {
        label: 'Spread',
        value: fmtPct(row.quote?.spread_pct, 1),
      },
    ],
  });
  if (intraday) {
    out.push({
      title: 'Premium target and lifecycle plan', layout: 'tiles',
      summary: 'Minute-bar option plan. Confirmed broker stops protect filled option quantity. Targets, trailing rules and the frozen mode deadline govern exits. Broker submission still requires fresh server-side risk, account and signal-eligibility checks.',
      stats: [
        { label: 'Target (requested)', value: `${n(metric(row, 'requested_target_points'))} pts` },
        { label: 'Target (effective)', value: `${n(metric(row, 'effective_target_points'))} pts` },
        { label: 'Runner trigger', value: n(row.runner_premium) },
        { label: 'Break-even after costs', value: n(row.breakeven_premium ?? metric(row, 'breakeven')) },
        { label: 'TSL starts at', value: n(row.profit_lock_premium ?? metric(row, 'profit_lock')) },
        { label: 'TSL distance', value: `${n(row.trail_points)} pts` },
        { label: 'Lock above break-even', value: `${n(row.lock_points)} pts` },
        { label: 'Initial hold', value: `${row.max_hold_bars ?? '—'} bars` },
        { label: 'Runner total limit', value: `${row.runner_max_bars ?? '—'} bars` },
        { label: 'Candle interval', value: `${row.timeframe_minutes ?? '—'} min` },
        { label: 'Variable costs', value: `${n(row.round_trip_cost_points ?? metric(row, 'estimated_variable_cost_points'))} pts` },
        { label: 'Fixed costs', value: fmtMoney(row.fixed_cost_inr ?? metric(row, 'estimated_fixed_cost_inr')) },
        { label: 'Target (net of costs)', value: fmtMoney(row.net_target_inr ?? metric(row, 'estimated_net_target_inr')) },
        { label: 'Risk (stop + costs)', value: fmtMoney(row.planned_risk_inr ?? metric(row, 'estimated_stop_loss_inr')) },
        { label: 'Net reward/risk', value: `${n(metric(row, 'net_reward_risk'))}x` },
        { label: 'ADX14', value: n(metric(row, 'adx'), 1) },
      ],
    });
  }
  if (row.outcome) {
    const o2 = row.outcome;
    out.push({
      title: o2.open ? 'Position, so far' : 'How it ended',
      layout: 'tiles',
      summary: o2.open
        ? `Open ${o2.held_days} of ${o2.held_days + o2.sessions_left} sessions.`
        : `Closed ${o2.reason.replace(/_/g, ' ')} after ${o2.held_days} sessions.`,
      stats: [
        { label: LABELS.entry, value: n(o2.entry_premium) },
        { label: o2.open ? LABELS.ltp : LABELS.exit, value: n(o2.exit_premium) },
        { label: 'Return', value: fmtPct(o2.return_pct, 1, true) },
        { label: LABELS.pnl, value: fmtPnl(o2.net) },
        { label: 'Sessions', value: String(o2.held_days) },
        { label: 'Spot now', value: n(o2.spot_out) },
      ],
    });
    if (o2.beta != null) {
      out.push({
        title: 'The hedge',
        layout: 'tiles',
        summary: 'Shown apart from the result on purpose: a single hedged '
          + 'figure hides whether the money came from the edge or from the '
          + 'hedge being on the right side of the tape.',
        stats: [
          { label: 'Beta', value: n(o2.beta) },
          {
            label: 'Market removed',
            value: fmtPnl(o2.market_pnl),
          },
          {
            label: 'Hedge cost',
            value: fmtMoney(o2.hedge_cost),
          },
          {
            label: 'Unhedged it would be',
            value: fmtPnl(o2.net_unhedged),
          },
        ],
      });
    }
  }
  if (row.reason) {
    out.push({
      title: 'Not armed because',
      layout: 'rows',
      stats: [{ label: '1', value: row.reason }],
    });
  }
  return out;
}

const STATUS: Record<string, BoardStatus> = {
  armed: 'armed', watching: 'watching', running: 'running', ended: 'ended',
  error: 'error',
};

export function snapbackRowToBoard(row: SnapbackRow): BoardSignal {
  const status: BoardStatus = STATUS[row.state] ?? 'watching';
  const o = row.outcome;
  const premium = price(row.premium);
  const entryPremium = o ? price(o.entry_premium) : premium;
  const stopPremium = price(row.stop_premium);
  const quantity = row.quantity || null;
  const plannedRisk = (
    entryPremium != null
    && stopPremium != null
    && stopPremium < entryPremium
    && quantity != null
  )
    ? (entryPremium - stopPremium) * quantity
    : null;
  return {
    id: row.signal_id,
    engine: 'snapback',
    historical: row.historical === true,
    underlying: row.symbol,
    instrument: instrument(row),
    // The OPTION is bought either way, so the row's direction is the thesis
    // about the underlying: a put into strength is a short thesis.
    direction: row.direction === 'BEARISH' ? 'short' : 'long',
    status,
    // `timestamp_ms` is the signal candle's OPEN. A minute-bar setup exists only
    // once that candle has closed, so the time shown is the close, matching the
    // daily lane where the stamp is the 15:30 close.
    atMs: (isMinuteBarMode(row) ? row.signal_close_ms : null) ?? row.timestamp_ms ?? null,
    // How old the quote behind `levels.ltp` is, computed by the server when it
    // served the row. Rows outlive their scan; an unlabelled old LTP reads as live.
    quoteAgeS: row.quote_age_s ?? null,
    levels: {
      // A replayed row has no live quote; its "last price" is what the replay
      // last valued the contract at, which for an OPEN one is exactly that.
      ltp: o ? (o.open ? price(o.exit_premium) : null) : price(row.quote?.ltp),
      // The PREMIUM ladder. The spot thesis lives in `sections` — what an
      // operator acts on here is what the contract costs and where it stops.
      entry: entryPremium,
      stop: stopPremium,
      trail: price(row.trail_premium),
      // The thesis' own objective: what this contract is worth if spot returns
      // to the mean. Priced at the entry's vol, like every other number here.
      target: price(row.target_premium),
      // Only a CLOSED trade has a realised exit. Putting one on a position
      // still inside its holding period would claim it was over.
      exit: o && !o.open ? price(o.exit_premium) : null,
    },
    sizing: {
      lots: row.lots || null,
      quantity,
      // Shared-board "At risk" means the planned loss if the displayed stop is
      // honoured. Keep premium outlay separate as deployed capital. When there
      // is no valid stop, risk is unknown rather than silently replaced by the
      // maximum possible option loss.
      // The minute-bar plan prices its own loss INCLUDING round-trip costs, and
      // the detail tile shows that same figure; the table must not quote a
      // smaller one (points x quantity alone).
      atRiskInr: isMinuteBarMode(row) && row.planned_risk_inr != null
        ? row.planned_risk_inr
        : plannedRisk,
      deployedInr: row.deployed_inr ?? null,
    },
    score: null,
    reason: row.execution_block_reason || row.execution_block_reasons?.join('; ') || row.reason,
    origin: origin(row),
    flags: row.metrics?.missing_setup_metrics
      ? [
          {
            label: (row.trading_mode || 'swing').toUpperCase(),
            tone: (row.trading_mode || 'swing').toLowerCase() === 'scalp' ? 'amber' : (row.trading_mode || 'swing').toLowerCase() === 'intraday' ? 'purple' : 'blue',
            hint: `${(row.trading_mode || 'swing').toUpperCase()} mode setup`,
          },
          { label: 'modelled', tone: 'amber', hint: 'Historical model; setup metric snapshots unavailable' },
        ]
      : flags(row),
    underlyingPrice: price(row.spot),
    delta: row.contract?.delta ?? null,
    // The row carries the strategy's planned premium levels. Execution still
    // revalidates the live quote and plan server-side; the board must not invent
    // a different price or sizing rule.
    planPriced: true,
    // Snapback rows carry their own horizon and premium-stop plan. The generic
    // manual ticket can place only rows the backend marks execution-eligible;
    // unsupported spreads/hedges stay visible as model/replay evidence.
    noTrailingStop: true,
    executionEligible: row.execution_eligible === true && !row.historical && !row.premium_is_modelled,
    sections: row.metrics?.missing_setup_metrics ? [] : sections(row),
  };
}

export function snapbackPositionToBoard(pos: SnapbackPosition, setup?: BoardSignal): BoardSignal {
  const closed = pos.status === 'CLOSED';
  const remaining = Math.max(0, pos.option_qty - pos.option_exit_qty);
  const quantity = closed ? pos.option_qty : remaining;
  const entry = price(pos.option_entry_price);
  const filled = entry != null && pos.option_qty > 0;
  const stop = price(pos.stop_premium);
  const brokerStop = !closed && pos.protection_state === 'ACTIVE' && pos.gtt_id > 0
    && pos.gtt_qty === remaining && remaining > 0 ? price(pos.gtt_stop) : null;
  const exit = closed && pos.option_exit_unknown_qty === 0 && pos.option_exit_qty > 0
    ? price(pos.option_exit_value / pos.option_exit_qty) : null;
  const sameContract = setup?.instrument.symbol === pos.option_symbol;
  const strike = sameContract ? setup!.instrument.strike : Number(parseInstrument(pos.option_symbol)?.strike);
  const target = typeof pos.rules.target_premium === 'number' ? price(pos.rules.target_premium) : null;
  return {
    id: `snapback-position:${pos.position_id}`, engine: 'snapback', historical: closed, underlying: pos.underlying,
    instrument: {
      symbol: pos.option_symbol, exchange: pos.option_exchange, kind: 'option',
      optionType: pos.option_type, expiry: pos.option_expiry || null, lotSize: pos.lot_size,
      strike: typeof strike === 'number' && Number.isFinite(strike) && strike > 0 ? strike : null,
      quoteKey: pos.option_symbol && pos.option_exchange ? `${pos.option_exchange}:${pos.option_symbol}` : null,
    },
    direction: pos.option_type === 'PE' ? 'short' : 'long', status: closed ? 'ended' : filled ? 'running' : 'watching',
    atMs: pos.opened_ms || null,
    levels: { entry, stop, target, exit,
      ltp: !closed && sameContract ? setup!.levels.ltp : null,
      trail: brokerStop != null && stop != null && brokerStop > stop ? brokerStop : null },
    sizing: { quantity: filled ? quantity : null, lots: filled && pos.lot_size > 0 ? quantity / pos.lot_size : null,
      deployedInr: entry != null ? entry * quantity : null,
      atRiskInr: entry != null && brokerStop != null ? Math.max(0, entry - brokerStop) * remaining : null },
    score: null, executionEligible: false, planPriced: true, noTrailingStop: true,
    reason: closed ? pos.exit_reason || 'Closed' : `Owned position · ${pos.status.toLowerCase()}`,
    origin: { label: filled ? 'FILLED' : 'OWNED', tone: 'blue', hint: filled
      ? 'Confirmed option fills from the durable Snapback registry; this is not a scan estimate.'
      : 'Strategy-owned entry record; confirmed fill evidence is unavailable.' },
    sections: [{ title: filled ? 'Confirmed Snapback position' : 'Owned Snapback entry', layout: 'tiles', stats: [
      { label: 'Filled quantity', value: filled ? fmtQty(pos.option_qty) : '—' },
      { label: 'Remaining quantity', value: fmtQty(remaining) },
      { label: 'Average entry', value: n(entry) },
      { label: 'Position state', value: pos.status },
      { label: 'Protection', value: closed ? 'Closed' : brokerStop != null ? 'Broker stop confirmed' : 'Protection not confirmed' },
      { label: 'Broker stop', value: n(brokerStop) },
      { label: 'Desired stop', value: n(price(pos.desired_stop)) },
      { label: 'Average exit', value: n(exit) },
    ] }, ...(sameContract ? setup!.sections : [])],
  };
}

export function snapbackRowsToBoard(rows: readonly SnapbackRow[], positions: readonly SnapbackPosition[] = []): BoardSignal[] {
  const owned = new Map<string, SnapbackPosition[]>();
  for (const pos of positions) {
    const id = pos.signal_id || pos.position_id;
    owned.set(id, [...(owned.get(id) ?? []), pos]);
  }
  const seen = new Set<string>();
  const signals = rows.map(row => {
    const setup = snapbackRowToBoard(row);
    const candidates = !row.historical ? owned.get(row.signal_id) : undefined;
    const pos = candidates?.find(p => p.status !== 'CLOSED') ?? candidates?.at(-1);
    if (!pos) return setup;
    seen.add(pos.position_id);
    return snapbackPositionToBoard(pos, setup);
  });
  for (const pos of positions) if (!seen.has(pos.position_id)) signals.push(snapbackPositionToBoard(pos));
  return signals;
}

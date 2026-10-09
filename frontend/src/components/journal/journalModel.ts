/**
 * The Journal's logic with no React in it: filters -> request parameters, plain-English sentences,
 * and the CSV. Pure functions, so every figure and sentence is checked by hand in the tests.
 *
 * Wording rule: the audience includes people who are learning how the app works, so every phrase
 * is plain English and every jargon term (SL, TSL, ADX, R) carries a one-line explanation nearby.
 */
import { toCsv, type CsvColumn } from '../../utils/csvExport';
import { EM_DASH, fmtDateTimeIST, fmtMoney, fmtPnl, fmtPx, fmtTimeIST } from '../../lib/format';
import {
  STRATEGY_IDS,
  type JournalMode, type JournalTrade, type Outcome, type StrategyId, type StrategyRow,
} from './types';

// ─── Filters -> request parameters ─────────────────────────────────────────────────────────────

export type RangePreset = 'today' | '7d' | '30d' | 'all' | 'custom';
export type SortKey = 'newest' | 'oldest' | 'pnl_desc' | 'pnl_asc' | 'hold_desc';

export interface JournalFilters {
  mode: JournalMode;
  strategy: '' | StrategyId;
  range: RangePreset;
  /** Only read when ``range`` is ``custom`` (YYYY-MM-DD). */
  dateFrom: string;
  dateTo: string;
  outcome: '' | Outcome;
  symbol: string;
  exitReason: string;
  sort: SortKey;
}

export const PAGE_SIZE = 25;
export const SKIPS_PAGE_SIZE = 15;
/** The backend's own ceiling on one page. */
export const EXPORT_PAGE_SIZE = 500;
/** Keeps a runaway export bounded: ten full pages of trades is years of this journal. */
export const EXPORT_MAX_TRADES = 5000;

export function defaultFilters(mode: JournalMode = 'live'): JournalFilters {
  return {
    mode, strategy: '', range: '30d', dateFrom: '', dateTo: '',
    outcome: '', symbol: '', exitReason: '', sort: 'newest',
  };
}

const IST_DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
});

/** Today's date in India, YYYY-MM-DD, whatever zone the browser is in. */
export function istDay(now: Date = new Date()): string {
  return IST_DAY.format(now);
}

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  const t = new Date(Date.UTC(y, (m || 1) - 1, (d || 1) + n));
  return t.toISOString().slice(0, 10);
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function resolveDates(f: JournalFilters, now: Date = new Date()): { date_from?: string; date_to?: string } {
  const today = istDay(now);
  switch (f.range) {
    case 'today': return { date_from: today, date_to: today };
    case '7d': return { date_from: addDays(today, -6), date_to: today };
    case '30d': return { date_from: addDays(today, -29), date_to: today };
    case 'custom':
      return {
        date_from: ISO_DAY.test(f.dateFrom) ? f.dateFrom : undefined,
        date_to: ISO_DAY.test(f.dateTo) ? f.dateTo : undefined,
      };
    default: return {};
  }
}

export type Params = Record<string, string | number | undefined>;

/** Filters every statistics endpoint understands: view, strategy and dates. */
export function statsParams(f: JournalFilters, now?: Date): Params {
  return { mode: f.mode, strategy: f.strategy || undefined, ...resolveDates(f, now) };
}

/** The comparison table always shows all three strategies, so it takes no strategy filter. */
export function byStrategyParams(f: JournalFilters, now?: Date): Params {
  return { mode: f.mode, ...resolveDates(f, now) };
}

export function tradesParams(f: JournalFilters, page: number, now?: Date): Params {
  return {
    ...statsParams(f, now),
    outcome: f.outcome || undefined,
    symbol: f.symbol.trim() || undefined,
    exit_reason: f.exitReason || undefined,
    sort: f.sort,
    limit: PAGE_SIZE,
    offset: Math.max(0, page) * PAGE_SIZE,
  };
}

export function skipsParams(f: JournalFilters, page: number, now?: Date): Params {
  return { ...statsParams(f, now), limit: SKIPS_PAGE_SIZE, offset: Math.max(0, page) * SKIPS_PAGE_SIZE };
}

/** ``?a=1&b=2`` with every empty value left out, or ``''`` when nothing is set. */
export function toQuery(params: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** True when a filter other than the date range narrows the list (drives the "clear filters" hint). */
export function isNarrowed(f: JournalFilters): boolean {
  return Boolean(f.strategy || f.outcome || f.symbol.trim() || f.exitReason);
}

// ─── Trade helpers ─────────────────────────────────────────────────────────────────────────────

export function tradePnl(t: Pick<JournalTrade, 'pnl' | 'net_pnl' | 'gross_pnl'>): number | null {
  return t.pnl ?? t.net_pnl ?? t.gross_pnl ?? null;
}

export type TradeOutcome = 'win' | 'loss' | 'breakeven' | 'open' | 'rejected' | 'unknown';

export function outcomeOf(t: JournalTrade): TradeOutcome {
  const status = String(t.status).toUpperCase();
  if (status === 'OPEN' || status === 'PENDING') return 'open';
  if (status === 'REJECTED') return 'rejected';
  const pnl = tradePnl(t);
  if (pnl === null) return 'unknown';
  if (pnl > 0) return 'win';
  if (pnl < 0) return 'loss';
  return 'breakeven';
}

/** 42m / 2h 5m / 1d 3h. */
export function fmtHold(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return EM_DASH;
  if (minutes < 1) return '<1m';
  const total = Math.round(minutes);
  if (total < 60) return `${total}m`;
  const h = Math.floor(total / 60);
  if (h < 24) return total % 60 ? `${h}h ${total % 60}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}

/** +1.50R / -1.00R: the P&L as a multiple of what the trade risked at the start. */
export function fmtR(r: number | null | undefined): string {
  if (r === null || r === undefined || !Number.isFinite(r)) return EM_DASH;
  const body = Math.abs(r).toFixed(2);
  if (Number(body) === 0) return '0.00R';
  return `${r < 0 ? '-' : '+'}${body}R`;
}

/** NIFTY 24500 CE, BANKNIFTY futures, or the raw symbol when nothing better is known. */
export function contractLabel(t: Pick<JournalTrade, 'underlying' | 'option_type' | 'strike' | 'symbol'>): string {
  const under = (t.underlying || '').trim();
  const kind = (t.option_type || '').toUpperCase();
  if ((kind === 'CE' || kind === 'PE') && under && t.strike) return `${under} ${Number(t.strike)} ${kind}`;
  if (kind === 'FUT' && under) return `${under} futures`;
  return t.symbol || under || EM_DASH;
}

export function sideLabel(side: string | null | undefined): string {
  const s = String(side || 'BUY').toUpperCase();
  return s === 'SELL' ? 'Sell' : 'Buy';
}

export function whenLabel(ts: number | null | undefined): string {
  return fmtDateTimeIST(ts, false);
}

/** The time a trade belongs to in the list: when it closed, else when it opened. */
export function tradeTime(t: JournalTrade): number | null {
  return t.exit_ts_ms ?? t.entry_ts_ms ?? t.signal_ts_ms ?? null;
}

export function modeBadge(mode: string): 'LIVE' | 'SIMULATION' {
  return String(mode).toUpperCase() === 'SIMULATION' ? 'SIMULATION' : 'LIVE';
}

// ─── Exit reasons, in plain English ────────────────────────────────────────────────────────────

export const EXIT_REASON_LABELS: Record<string, string> = {
  SL: 'Stop-loss hit',
  TSL: 'Trailing stop hit',
  TARGET: 'Target reached',
  EOD: 'End-of-day exit',
  TIME_STOP: 'Time stop',
  SIGNAL_EXIT: 'Exit signal',
  MANUAL: 'Manual exit',
  SQUARE_OFF: 'Square-off',
  EXPIRY: 'Expiry exit',
  HEDGE: 'Hedge problem',
  BROKER_STOP: 'Broker stop fired',
  RECONCILED: 'Closed outside Sterling',
  OTHER: 'Other',
};

/** One line a non-expert can read: what this way of ending a trade means. */
export const EXIT_REASON_HELP: Record<string, string> = {
  SL: 'SL is the stop-loss: a safety limit set when the trade started. The price fell to it, so the app sold to keep the loss small.',
  TSL: 'TSL is the trailing stop: the safety limit is moved up behind the price as the trade gains. The price then turned back and touched it, keeping what was left of the gain.',
  TARGET: 'The price reached the profit goal set at the start, so the app sold and kept the gain.',
  EOD: 'Trades that are not meant to stay overnight are closed near the end of the market day.',
  TIME_STOP: 'The trade was given a fixed amount of time to work. The time ran out, so the app sold at whatever price it was.',
  SIGNAL_EXIT: 'The strategy’s own exit signal fired (for example the trend turned) before the stop or the target was reached.',
  MANUAL: 'A person closed the trade by hand.',
  SQUARE_OFF: 'A “close everything” action ended it (a square-off, or the kill switch).',
  EXPIRY: 'The option was about to expire, so it was closed first to avoid expiry-day risk.',
  HEDGE: 'The protective hedge leg had a problem, so the trade was closed.',
  BROKER_STOP: 'The stop order held at the broker fired. It is the same safety limit, just kept by the broker rather than by Sterling.',
  RECONCILED: 'The position was closed outside Sterling (for example in the broker’s own app) and the journal caught up afterwards.',
  OTHER: 'Closed for a reason the journal could not classify.',
};

export function exitReasonLabel(reason: string, apiLabel?: string): string {
  const key = (reason || 'OTHER').toUpperCase();
  return apiLabel || EXIT_REASON_LABELS[key] || key;
}

export function exitReasonHelp(reason: string): string {
  return EXIT_REASON_HELP[(reason || 'OTHER').toUpperCase()] || EXIT_REASON_HELP.OTHER;
}

/** "lost money on ..." reads naturally with these; the API's own labels are singular. */
const DRAG_PHRASE: Record<string, string> = {
  SL: 'stop-outs',
  TSL: 'trailing-stop exits',
  TARGET: 'target exits',
  EOD: 'end-of-day exits',
  TIME_STOP: 'time-stop exits',
  SIGNAL_EXIT: 'exit-signal exits',
  MANUAL: 'manual exits',
  SQUARE_OFF: 'square-offs',
  EXPIRY: 'expiry exits',
  HEDGE: 'hedge problems',
  BROKER_STOP: 'broker stop-outs',
  RECONCILED: 'trades closed outside Sterling',
  OTHER: 'other exits',
};

// ─── The strategy comparison sentence ──────────────────────────────────────────────────────────

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * One plain sentence over the three strategy rows:
 * "Snapback is ahead by ₹1,200.00; Gamma Unwind lost money on stop-outs."
 */
export function comparisonSentence(rows: readonly StrategyRow[]): string {
  const traded = rows.filter((r) => r.trades > 0);
  if (!traded.length) return 'No strategy has closed a trade yet in this period.';
  const net = (r: StrategyRow) => r.net_pnl ?? 0;
  const ranked = [...traded].sort((a, b) => net(b) - net(a));
  const leader = ranked[0];

  let lead: string;
  if (ranked.length === 1) {
    lead = `${leader.label} is the only strategy with closed trades so far: ${fmtPnl(net(leader))} over ${plural(leader.trades, 'trade')}`;
  } else if (net(leader) > 0) {
    lead = `${leader.label} is ahead by ${fmtMoney(net(leader) - net(ranked[1]))}`;
  } else {
    lead = `No strategy is making money yet; ${leader.label} is the closest to break-even at ${fmtPnl(net(leader))}`;
  }

  const clauses = [lead];
  for (const r of ranked.slice(1)) {
    if (net(r) >= 0) continue;
    const drag = r.biggest_drag;
    clauses.push(drag
      ? `${r.label} lost money on ${DRAG_PHRASE[drag.reason] ?? drag.label.toLowerCase()}`
      : `${r.label} is down ${fmtMoney(Math.abs(net(r)))}`);
  }
  let text = `${clauses.join('; ')}.`;

  const idle = rows.filter((r) => r.trades === 0).map((r) => r.label);
  if (idle.length) text += ` ${idle.join(' and ')} ${idle.length === 1 ? 'has' : 'have'} not closed a trade yet.`;
  return text;
}

/** The strategy to highlight in the table: the one with the best net P&L among those that traded. */
export function leaderOf(rows: readonly StrategyRow[]): string | null {
  const traded = rows.filter((r) => r.trades > 0);
  if (!traded.length) return null;
  return [...traded].sort((a, b) => (b.net_pnl ?? 0) - (a.net_pnl ?? 0))[0].strategy;
}

/** The three known strategies in a fixed order, so the table does not reshuffle on each refresh. */
export function orderedStrategies(rows: readonly StrategyRow[]): StrategyRow[] {
  const byId = new Map(rows.map((r) => [String(r.strategy), r]));
  return STRATEGY_IDS.map((id) => byId.get(id)).filter((r): r is StrategyRow => Boolean(r));
}

// ─── "What went wrong" ─────────────────────────────────────────────────────────────────────────

const WRONG_BY_REASON: Record<string, string> = {
  SL: 'The price moved against the trade and fell to its stop-loss, so the app sold to stop the loss growing.',
  TSL: 'The stop had been raised behind the price, but the price turned back and hit it before the trade could gain, so it closed at a loss.',
  TARGET: 'The trade reached its target, but the price it sold at was not far enough above the entry to cover the cost.',
  EOD: 'Neither the stop-loss nor the target was reached before the market day ended, so the app closed it at the end of the day at a loss.',
  TIME_STOP: 'The trade did not move enough in the time it was given, so the app closed it at a small loss rather than wait longer.',
  SIGNAL_EXIT: 'The strategy’s exit signal fired while the trade was still behind, so the app got out early.',
  MANUAL: 'It was closed by hand before it reached its target.',
  SQUARE_OFF: 'A “close everything” action ended it while the trade was behind.',
  EXPIRY: 'The option was closed ahead of expiry while it was behind.',
  HEDGE: 'A problem with the hedge forced the trade to close.',
  BROKER_STOP: 'The stop order held at the broker was hit.',
  RECONCILED: 'The position was closed outside Sterling, so the journal can only report the result, not the reason.',
  OTHER: 'The trade closed at a loss; the journal could not classify the reason.',
};

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : Number(v);
  return v !== null && v !== '' && v !== undefined && Number.isFinite(n) ? n : null;
}

/**
 * A short explanation of why a losing trade lost, from its exit reason and the figures and signal
 * fields the journal kept. ``null`` for a trade that is open, flat or a winner.
 */
export function whatWentWrong(t: JournalTrade): string | null {
  if (String(t.status).toUpperCase() !== 'CLOSED') return null;
  const pnl = tradePnl(t);
  if (pnl === null || pnl >= 0) return null;

  const reason = (t.exit_reason || 'OTHER').toUpperCase();
  const out: string[] = [];
  const gross = num(t.gross_pnl);
  const charges = num(t.charges);

  if (gross !== null && gross >= 0 && charges !== null && charges > 0) {
    out.push(`The trade made ${fmtMoney(gross)} before charges, but ${fmtMoney(charges)} of charges and taxes turned it into a loss.`);
  } else {
    out.push(WRONG_BY_REASON[reason] ?? WRONG_BY_REASON.OTHER);
    const hold = num(t.hold_minutes);
    if (hold !== null && hold < 10 && (reason === 'SL' || reason === 'TSL')) {
      out.push(`It went wrong fast (${fmtHold(hold)} after entry), so the price turned against it almost at once.`);
    }
    const mfe = num(t.mfe);
    if (mfe !== null && mfe >= 1) {
      out.push(`At its best it was ${fmtMoney(mfe)} ahead before the price turned.`);
    }
    const r = num(t.r_multiple);
    if (r !== null && r < -1.2) {
      out.push(`It lost ${Math.abs(r).toFixed(1)} times what it planned to risk, which can happen when the price jumps past the stop.`);
    }
  }

  const ctx = (t.signal_context || {}) as Record<string, unknown>;
  const strategy = String(t.strategy);
  if (strategy === 'supertrend') {
    const aligned = num(ctx.aligned);
    if (aligned !== null && aligned < 3) out.push(`Only ${aligned} of 3 SuperTrend lines agreed when it entered.`);
    const adx = num(ctx.adx);
    if (adx !== null && adx < 20) out.push(`The trend was weak when it entered (ADX ${adx.toFixed(0)}; below 20 means little direction).`);
  } else if (strategy === 'gamma') {
    const warnings = Array.isArray(ctx.warnings) ? ctx.warnings.map(String).filter(Boolean) : [];
    if (warnings.length) out.push(`The signal carried warnings: ${warnings.join(', ')}.`);
  } else if (strategy === 'snapback') {
    if (/weak/i.test(String(ctx.strength ?? ''))) out.push('The signal was rated weak when it entered.');
  }
  return out.join(' ');
}

// ─── Why it entered ────────────────────────────────────────────────────────────────────────────

export interface Fact { label: string; value: string }

const titleCase = (s: string) => s.replace(/[_-]+/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

function pct(n: number, d = 1): string {
  return `${n.toFixed(d)}%`;
}

/**
 * The signal fields behind an entry as label/value pairs a person can read. Known fields get plain
 * names; any other plain value is still shown under a tidied key, so nothing the journal kept is lost.
 */
export function signalFacts(strategy: string, ctx: Record<string, unknown> | null | undefined): Fact[] {
  const c = ctx || {};
  const facts: Fact[] = [];
  const used = new Set<string>();
  const add = (key: string, label: string, fmt: (v: unknown) => string | null) => {
    if (!(key in c) || c[key] === null || c[key] === undefined || c[key] === '') return;
    used.add(key);
    const value = fmt(c[key]);
    if (value) facts.push({ label, value });
  };
  const text = (v: unknown) => String(v);
  const numeric = (f: (n: number) => string) => (v: unknown) => { const n = num(v); return n === null ? null : f(n); };
  const list = (v: unknown) => (Array.isArray(v) && v.length ? v.map(String).join(', ') : null);
  const yesNo = (v: unknown) => (v ? 'Yes' : 'No');

  if (strategy === 'supertrend') {
    add('regime', 'Market mood', (v) => ({ BULL: 'Bullish', BEAR: 'Bearish' } as Record<string, string>)[String(v).toUpperCase()] ?? titleCase(String(v)));
    add('direction', 'Direction', (v) => (String(v) === 'short' ? 'Short (bet on a fall)' : 'Long (bet on a rise)'));
    add('aligned', 'SuperTrend lines agreeing', numeric((n) => `${n} of 3`));
    add('adx', 'Trend strength (ADX)', numeric((n) => n.toFixed(0)));
    add('atr_pct', 'Volatility rank', numeric((n) => `${n.toFixed(0)} out of 100`));
    add('score', 'Signal score', numeric((n) => n.toFixed(0)));
    add('source', 'Chart used', (v) => (String(v) === 'derivatives' ? 'The option’s own chart' : 'The index chart'));
    add('underlying_spot', 'Index at the signal', numeric((n) => fmtPx(n)));
    add('entry_delta', 'Option delta', numeric((n) => n.toFixed(2)));
    add('stop_mode', 'Stop type', (v) => titleCase(String(v)));
    add('exit_mode', 'Exit style', (v) => titleCase(String(v)));
    used.add('alignment'); used.add('vehicle'); used.add('underlying_trail');
  } else if (strategy === 'gamma') {
    add('tier', 'Signal strength', (v) => (['ENTRY', 'E1'].includes(String(v).toUpperCase()) ? 'E1 entry' : `${String(v).toUpperCase()} alert`));
    add('oi_drop_pct', 'Open interest', numeric((n) => (n >= 0 ? `fell ${pct(n)}` : `rose ${pct(-n)}`)));
    add('vol_ratio', 'Trading volume', numeric((n) => `${n.toFixed(1)}x its average`));
    add('zone_edge', 'Zone edge', numeric((n) => fmtPx(n)));
    add('dte', 'Days to expiry', text);
    add('gates_passed', 'Checks passed', list);
    add('quadrant', 'Quadrant', (v) => titleCase(String(v)));
    add('hold', 'Planned hold', (v) => titleCase(String(v)));
    add('strong', 'Strong signal', yesNo);
    add('reason', 'Signal note', text);
    add('warnings', 'Warnings', list);
    used.add('gate_reasons'); used.add('base_low'); used.add('base_high'); used.add('signal_ltp');
  } else if (strategy === 'snapback') {
    add('lane', 'Lane', (v) => titleCase(String(v)));
    add('side', 'Move being faded', (v) => ({ fade_up: 'An up-move', fade_down: 'A down-move' } as Record<string, string>)[String(v)] ?? titleCase(String(v)));
    add('strength', 'Signal strength', (v) => titleCase(String(v)));
    add('stretch', 'Stretch from normal', numeric((n) => String(Number(n.toFixed(2)))));
    add('distance_pct', 'Distance from the mean', numeric((n) => pct(n, 2)));
    add('mean_target', 'Mean (where it should snap back to)', numeric((n) => fmtPx(n)));
    add('spot', 'Index at the signal', numeric((n) => fmtPx(n)));
    add('assumed_iv_pct', 'Assumed volatility', numeric((n) => pct(n)));
    add('hold_days', 'Planned hold', (v) => `${v} day${Number(v) === 1 ? '' : 's'}`);
    add('risk_amount', 'Risk budget', numeric((n) => fmtMoney(n)));
    add('cash_required', 'Cash needed', numeric((n) => fmtMoney(n)));
    add('margin_required', 'Margin needed', numeric((n) => fmtMoney(n)));
    const hedge = c.hedge as Record<string, unknown> | undefined;
    if (hedge && hedge.symbol) {
      facts.push({ label: 'Hedge', value: `${hedge.symbol}${hedge.qty ? ` × ${hedge.qty}` : ''}` });
    }
    used.add('hedge'); used.add('rules'); used.add('direction'); used.add('entry_quote'); used.add('limit_price');
  }

  for (const [key, value] of Object.entries(c)) {
    if (used.has(key) || key.endsWith('_ts_ms')) continue;
    if (value === null || value === undefined || value === '' || typeof value === 'object') continue;
    facts.push({ label: titleCase(key), value: String(value) });
  }
  return facts;
}

// ─── Timeline ──────────────────────────────────────────────────────────────────────────────────

export type StepTone = 'info' | 'good' | 'bad' | 'neutral';

export const TIMELINE_STEPS: Record<string, { label: string; tone: StepTone }> = {
  SIGNAL: { label: 'Signal seen', tone: 'info' },
  ORDER_SUBMITTED: { label: 'Order placed', tone: 'info' },
  FILLED: { label: 'Order filled', tone: 'info' },
  PROTECTION_PLACED: { label: 'Safety stop placed', tone: 'neutral' },
  TSL_MOVED: { label: 'Stop moved', tone: 'neutral' },
  TARGET_HIT: { label: 'Target reached', tone: 'good' },
  EXIT_SUBMITTED: { label: 'Exit order placed', tone: 'info' },
  EXIT_FILLED: { label: 'Exit filled', tone: 'info' },
  PARTIAL_EXIT: { label: 'Part of the position sold', tone: 'info' },
  HEDGE_FILLED: { label: 'Hedge filled', tone: 'neutral' },
  REJECTED: { label: 'Order rejected', tone: 'bad' },
  FAILED: { label: 'Something failed', tone: 'bad' },
  NOTE: { label: 'Note', tone: 'neutral' },
};

export function timelineStep(type: string): { label: string; tone: StepTone } {
  return TIMELINE_STEPS[String(type).toUpperCase()] ?? { label: titleCase(String(type).toLowerCase()), tone: 'neutral' };
}

// ─── CSV ───────────────────────────────────────────────────────────────────────────────────────

/** A spreadsheet runs a cell that starts with = + - @ as a formula; a leading quote makes it text. */
function safeText(s: string): string {
  return /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
}

const day = (ts: number | null | undefined): string => {
  if (!ts) return '';
  const d = istDay(new Date(ts < 1e11 ? ts * 1000 : ts));
  return d;
};

export const TRADE_CSV_COLUMNS: CsvColumn<JournalTrade>[] = [
  { header: 'Date', value: (t) => day(tradeTime(t)) },
  { header: 'Time (IST)', value: (t) => { const v = tradeTime(t); return v ? fmtTimeIST(v, false) : ''; } },
  { header: 'Mode', value: (t) => modeBadge(t.mode) },
  { header: 'Strategy', value: (t) => safeText(t.strategy_label || String(t.strategy)) },
  { header: 'Contract', value: (t) => safeText(contractLabel(t)) },
  { header: 'Symbol', value: (t) => safeText(t.symbol || '') },
  { header: 'Side', value: (t) => sideLabel(t.side) },
  { header: 'Qty', value: (t) => t.qty },
  { header: 'Entry', value: (t) => t.entry_price },
  { header: 'Exit', value: (t) => t.exit_price },
  { header: 'Gross P&L', value: (t) => t.gross_pnl },
  { header: 'Charges', value: (t) => t.charges },
  { header: 'Net P&L', value: (t) => tradePnl(t) },
  { header: 'R', value: (t) => t.r_multiple },
  { header: 'Hold (minutes)', value: (t) => t.hold_minutes },
  { header: 'Exit reason', value: (t) => (t.exit_reason ? exitReasonLabel(t.exit_reason, t.exit_reason_label) : '') },
  { header: 'Status', value: (t) => t.status },
  { header: 'Story', value: (t) => safeText(t.narrative || '') },
];

export function tradesCsv(trades: readonly JournalTrade[]): string {
  return toCsv(trades, TRADE_CSV_COLUMNS);
}

export function csvFilename(mode: JournalMode, now: Date = new Date()): string {
  return `sterling-journal-${mode}-${istDay(now)}.csv`;
}

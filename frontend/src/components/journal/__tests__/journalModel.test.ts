import { describe, it, expect } from 'vitest';
import {
  addDays, byStrategyParams, comparisonSentence, contractLabel, csvFilename, defaultFilters, exitReasonHelp,
  fmtHold, fmtR, istDay, isNarrowed, leaderOf, orderedStrategies, outcomeOf, resolveDates, signalFacts,
  skipsParams, statsParams, timelineStep, toQuery, tradePnl, tradesCsv, tradesParams, whatWentWrong, PAGE_SIZE,
} from '../journalModel';
import { BY_STRATEGY, LOSS, OPEN, WIN, strategyRow, trade } from './fixtures';

// 05:30 UTC on 7 Oct is 11:00 IST on 7 Oct; 20:00 UTC on 6 Oct is already 01:30 IST on 7 Oct.
const NOW = new Date('2026-10-07T05:30:00Z');

describe('dates (IST)', () => {
  it('reads today in India, not in the browser zone', () => {
    expect(istDay(NOW)).toBe('2026-10-07');
    expect(istDay(new Date('2026-10-06T20:00:00Z'))).toBe('2026-10-07');
  });

  it('adds days across a month end', () => {
    expect(addDays('2026-10-01', -1)).toBe('2026-09-30');
    expect(addDays('2026-10-07', -29)).toBe('2026-09-08');
  });

  it('resolves each range preset', () => {
    const f = defaultFilters();
    expect(resolveDates({ ...f, range: 'today' }, NOW)).toEqual({ date_from: '2026-10-07', date_to: '2026-10-07' });
    expect(resolveDates({ ...f, range: '7d' }, NOW)).toEqual({ date_from: '2026-10-01', date_to: '2026-10-07' });
    expect(resolveDates({ ...f, range: '30d' }, NOW)).toEqual({ date_from: '2026-09-08', date_to: '2026-10-07' });
    expect(resolveDates({ ...f, range: 'all' }, NOW)).toEqual({});
  });

  it('passes only well-formed custom dates', () => {
    const f = { ...defaultFilters(), range: 'custom' as const, dateFrom: '2026-10-01', dateTo: 'garbage' };
    expect(resolveDates(f, NOW)).toEqual({ date_from: '2026-10-01', date_to: undefined });
  });
});

describe('filters -> request params', () => {
  const base = { ...defaultFilters('simulation'), range: '7d' as const };

  it('maps the view, strategy and dates for the statistics endpoints', () => {
    expect(statsParams({ ...base, strategy: 'gamma' }, NOW)).toEqual({
      mode: 'simulation', strategy: 'gamma', date_from: '2026-10-01', date_to: '2026-10-07',
    });
  });

  it('leaves the strategy off the comparison, which always shows all three', () => {
    const p = byStrategyParams({ ...base, strategy: 'gamma' }, NOW);
    expect(p).toEqual({ mode: 'simulation', date_from: '2026-10-01', date_to: '2026-10-07' });
    expect('strategy' in p).toBe(false);
  });

  it('adds the trade-only filters, sort and paging to the trades request', () => {
    const f = { ...base, mode: 'all' as const, outcome: 'loss' as const, symbol: ' nifty ', exitReason: 'SL', sort: 'pnl_asc' as const };
    expect(tradesParams(f, 2, NOW)).toEqual({
      mode: 'all', strategy: undefined, date_from: '2026-10-01', date_to: '2026-10-07',
      outcome: 'loss', symbol: 'nifty', exit_reason: 'SL', sort: 'pnl_asc', limit: PAGE_SIZE, offset: 2 * PAGE_SIZE,
    });
  });

  it('pages the skipped signals separately', () => {
    expect(skipsParams(base, 1, NOW)).toMatchObject({ mode: 'simulation', limit: 15, offset: 15 });
  });

  it('drops empty values from the query string', () => {
    expect(toQuery({ mode: 'live', strategy: undefined, outcome: '', limit: 25, offset: 0 })).toBe('?mode=live&limit=25&offset=0');
    expect(toQuery({ a: undefined })).toBe('');
  });

  it('knows when a non-date filter narrows the list', () => {
    expect(isNarrowed(defaultFilters())).toBe(false);
    expect(isNarrowed({ ...defaultFilters(), symbol: ' ' })).toBe(false);
    expect(isNarrowed({ ...defaultFilters(), outcome: 'win' })).toBe(true);
  });
});

describe('trade helpers', () => {
  it('takes net P&L, else gross, else nothing', () => {
    expect(tradePnl(WIN)).toBe(2187.5);
    expect(tradePnl({ pnl: null, net_pnl: null, gross_pnl: 12 })).toBe(12);
    expect(tradePnl({ pnl: null, net_pnl: null, gross_pnl: null })).toBeNull();
  });

  it('classifies outcomes', () => {
    expect(outcomeOf(WIN)).toBe('win');
    expect(outcomeOf(LOSS)).toBe('loss');
    expect(outcomeOf(OPEN)).toBe('open');
    expect(outcomeOf(trade({ status: 'REJECTED' }))).toBe('rejected');
    expect(outcomeOf(trade({ pnl: null, net_pnl: null, gross_pnl: null }))).toBe('unknown');
    expect(outcomeOf(trade({ pnl: 0, net_pnl: 0 }))).toBe('breakeven');
  });

  it('formats hold time, R and contracts', () => {
    expect(fmtHold(0.4)).toBe('<1m');
    expect(fmtHold(42)).toBe('42m');
    expect(fmtHold(125)).toBe('2h 5m');
    expect(fmtHold(120)).toBe('2h');
    expect(fmtHold(27 * 60)).toBe('1d 3h');
    expect(fmtHold(null)).toBe('—');
    expect(fmtR(1.5)).toBe('+1.50R');
    expect(fmtR(-1)).toBe('-1.00R');
    expect(fmtR(null)).toBe('—');
    expect(contractLabel(WIN)).toBe('NIFTY 25000 CE');
    expect(contractLabel(trade({ option_type: 'FUT', strike: null }))).toBe('NIFTY futures');
    expect(contractLabel(trade({ underlying: '', option_type: '', strike: null, symbol: 'RELIANCE' }))).toBe('RELIANCE');
  });

  it('explains every exit reason, with a fallback', () => {
    expect(exitReasonHelp('SL')).toMatch(/stop-loss/i);
    expect(exitReasonHelp('TSL')).toMatch(/trailing/i);
    expect(exitReasonHelp('NOPE')).toBe(exitReasonHelp('OTHER'));
  });

  it('gives the timeline plain names', () => {
    expect(timelineStep('PROTECTION_PLACED').label).toBe('Safety stop placed');
    expect(timelineStep('SOMETHING_NEW').label).toBe('Something New');
  });
});

describe('comparison sentence', () => {
  it('names the leader, the gap and what the loser lost money on', () => {
    expect(comparisonSentence(BY_STRATEGY.strategies)).toBe(
      'Snapback is ahead by ₹4,260.00; Gamma Unwind lost money on stop-outs. Supertrend has not closed a trade yet.',
    );
  });

  it('falls back to the loss figure when no drag is known', () => {
    const rows = [
      strategyRow('snapback', 'Snapback', { trades: 2, net_pnl: 500 }),
      strategyRow('gamma', 'Gamma Unwind', { trades: 1, net_pnl: -300, biggest_drag: null }),
      strategyRow('supertrend', 'Supertrend', { trades: 1, net_pnl: 100 }),
    ];
    expect(comparisonSentence(rows)).toBe('Snapback is ahead by ₹400.00; Gamma Unwind is down ₹300.00.');
  });

  it('does not claim a lead when everyone is losing', () => {
    const rows = [
      strategyRow('snapback', 'Snapback', { trades: 2, net_pnl: -100 }),
      strategyRow('gamma', 'Gamma Unwind', { trades: 1, net_pnl: -300, biggest_drag: null }),
    ];
    expect(comparisonSentence(rows)).toMatch(/^No strategy is making money yet; Snapback is the closest to break-even at -₹100\.00; Gamma Unwind is down ₹300\.00\./);
  });

  it('handles a single traded strategy and none at all', () => {
    expect(comparisonSentence([strategyRow('snapback', 'Snapback', { trades: 1, net_pnl: 250 })]))
      .toBe('Snapback is the only strategy with closed trades so far: +₹250.00 over 1 trade.');
    expect(comparisonSentence(BY_STRATEGY.strategies.map((r) => ({ ...r, trades: 0 }))))
      .toBe('No strategy has closed a trade yet in this period.');
  });

  it('picks the leader and keeps a fixed row order', () => {
    expect(leaderOf(BY_STRATEGY.strategies)).toBe('snapback');
    expect(leaderOf([])).toBeNull();
    const shuffled = [BY_STRATEGY.strategies[2], BY_STRATEGY.strategies[0], BY_STRATEGY.strategies[1]];
    expect(orderedStrategies(shuffled).map((r) => r.strategy)).toEqual(['supertrend', 'snapback', 'gamma']);
  });
});

describe('what went wrong', () => {
  it('is only for closed losses', () => {
    expect(whatWentWrong(WIN)).toBeNull();
    expect(whatWentWrong(OPEN)).toBeNull();
    expect(whatWentWrong(trade({ pnl: 0, net_pnl: 0, gross_pnl: 0 }))).toBeNull();
  });

  it('combines the exit reason, the quick loss, the near-miss and the signal warnings', () => {
    const text = whatWentWrong(LOSS) ?? '';
    expect(text).toMatch(/fell to its stop-loss/);
    expect(text).toMatch(/went wrong fast \(7m after entry\)/);
    expect(text).toMatch(/₹300\.00 ahead/);
    expect(text).toMatch(/warnings: thin book/);
  });

  it('blames charges when the trade made money before them', () => {
    const t = trade({ gross_pnl: 40, charges: 90, net_pnl: -50, pnl: -50, exit_reason: 'TARGET' });
    expect(whatWentWrong(t)).toMatch(/made ₹40\.00 before charges, but ₹90\.00 of charges/);
  });

  it('reads Supertrend entry quality', () => {
    const t = trade({
      strategy: 'supertrend', exit_reason: 'EOD', pnl: -200, net_pnl: -200, gross_pnl: -150, charges: 50, r_multiple: -1.5,
      hold_minutes: 200, mfe: 0, signal_context: { aligned: 2, adx: 14 },
    });
    const text = whatWentWrong(t) ?? '';
    expect(text).toMatch(/end of the day/);
    expect(text).toMatch(/Only 2 of 3 SuperTrend lines/);
    expect(text).toMatch(/ADX 14/);
    expect(text).toMatch(/1\.5 times what it planned to risk/);
  });
});

describe('signal facts', () => {
  it('names the Snapback fields in plain words', () => {
    const facts = signalFacts('snapback', WIN.signal_context);
    expect(facts).toEqual(expect.arrayContaining([
      { label: 'Lane', value: 'Intraday' },
      { label: 'Move being faded', value: 'A down-move' },
      { label: 'Signal strength', value: 'Strong' },
      { label: 'Distance from the mean', value: '0.80%' },
    ]));
  });

  it('names the Supertrend and Gamma fields', () => {
    expect(signalFacts('supertrend', OPEN.signal_context)).toEqual(expect.arrayContaining([
      { label: 'Market mood', value: 'Bullish' },
      { label: 'SuperTrend lines agreeing', value: '3 of 3' },
      { label: 'Trend strength (ADX)', value: '28' },
      { label: 'Chart used', value: 'The option’s own chart' },
    ]));
    expect(signalFacts('gamma', LOSS.signal_context)).toEqual(expect.arrayContaining([
      { label: 'Signal strength', value: 'E1 entry' },
      { label: 'Open interest', value: 'fell 6.2%' },
      { label: 'Trading volume', value: '2.1x its average' },
      { label: 'Checks passed', value: 'f1, f2' },
    ]));
  });

  it('keeps unknown plain fields, skips nested ones and timestamps', () => {
    const facts = signalFacts('snapback', { extra_note: 'hello', nested: { a: 1 }, signal_ts_ms: 5 });
    expect(facts).toEqual([{ label: 'Extra Note', value: 'hello' }]);
    expect(signalFacts('snapback', null)).toEqual([]);
  });
});

describe('csv', () => {
  it('writes one row per trade with the P&L, reason and story', () => {
    const csv = tradesCsv([WIN, LOSS]);
    const [header, first, second] = csv.split('\r\n');
    expect(header).toBe('Date,Time (IST),Mode,Strategy,Contract,Symbol,Side,Qty,Entry,Exit,Gross P&L,Charges,Net P&L,R,Hold (minutes),Exit reason,Status,Story');
    expect(first).toContain('2026-10-06,10:30,SIMULATION,Snapback,NIFTY 25000 CE,NIFTY2610625000CE,Buy,75,120,150,2250,62.5,2187.5,1.2,60,Target reached,CLOSED');
    expect(second).toContain(',LIVE,Gamma Unwind,');
    expect(second).toContain('-1860');
  });

  it('quotes commas and leaves absent numbers empty', () => {
    const csv = tradesCsv([trade({ narrative: 'a, b', exit_price: null })]);
    expect(csv).toContain('"a, b"');
    expect(csv.split('\r\n')[1]).toContain(',120,,2250,');
  });

  it('keeps a spreadsheet from running text as a formula', () => {
    const csv = tradesCsv([trade({ narrative: '=HYPERLINK("x")' })]);
    expect(csv).toContain(`"'=HYPERLINK(""x"")"`);
  });

  it('names the file by view and day', () => {
    expect(csvFilename('simulation', NOW)).toBe('sterling-journal-simulation-2026-10-07.csv');
  });
});

import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within, fireEvent, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({ useEngineActivity: () => ({ data: { market_open: true } }) }));

import { TradesSection } from '../TradesSection';
import { numbersFor, plannedRisk } from '../TradeStory';
import { defaultFilters, emptyTradesMessage, type JournalFilters } from '../journalModel';
import { LOSS, OPEN, WIN, page, trade } from './fixtures';
import { paramsOf, renderWithClient, routeGet, sim } from './testkit';

/** Holds the filters like the Journal tab does, so a filter change really re-requests. */
function Harness({ initial = sim(), download = vi.fn(), reset = vi.fn() }: {
  initial?: JournalFilters; download?: (f: string, c: string) => void; reset?: () => void;
}) {
  const [filters, setFilters] = useState(initial);
  return (
    <TradesSection
      filters={filters}
      patch={(p) => setFilters((f) => ({ ...f, ...p }))}
      reset={() => { reset(); setFilters((f) => ({ ...defaultFilters(f.mode), range: f.range, dateFrom: f.dateFrom, dateTo: f.dateTo })); }}
      download={download}
    />
  );
}

beforeEach(() => { get.mockReset(); routeGet(get); });

describe('trades table', () => {
  it('shows each trade with its view, contract, prices, coloured P&L and exit reason', async () => {
    renderWithClient(<Harness />);
    const win = await screen.findByTestId('trade-row-SB-1');
    expect(within(win).getByText('SIMULATION')).toBeInTheDocument();
    expect(within(win).getByText('Snapback')).toBeInTheDocument();
    expect(within(win).getByText('NIFTY 25000 CE')).toBeInTheDocument();
    expect(within(win).getByText('Buy')).toBeInTheDocument();
    expect(within(win).getByText('75')).toBeInTheDocument();
    expect(within(win).getByText('120.00')).toBeInTheDocument();
    expect(within(win).getByText('150.00')).toBeInTheDocument();
    const pnl = within(win).getByText('+₹2,187.50');
    expect(pnl.style.color).toBe('var(--k-green)');
    expect(within(win).getByText('+1.20R')).toBeInTheDocument();
    expect(within(win).getByText('1h')).toBeInTheDocument();
    expect(within(win).getByText('Target reached')).toHaveAttribute('title', expect.stringMatching(/profit goal/));

    const loss = screen.getByTestId('trade-row-GU-1');
    expect(within(loss).getByText('LIVE')).toBeInTheDocument();
    expect(within(loss).getByText('-₹1,860.00').style.color).toBe('var(--k-red)');
    expect(within(loss).getByText('Stop-loss hit')).toBeInTheDocument();
    expect(loss).toHaveAttribute('data-outcome', 'loss');
  });

  it('shows an open trade as Open, with no exit and no P&L yet', async () => {
    renderWithClient(<Harness />);
    const open = await screen.findByTestId('trade-row-ST-1');
    expect(open).toHaveAttribute('data-outcome', 'open');
    expect(within(open).getByText('Open')).toBeInTheDocument();
    expect(within(open).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('labels every cell so each row reflows into a card on a narrow screen', async () => {
    renderWithClient(<Harness />);
    const row = await screen.findByTestId('trade-row-SB-1');
    const labels = Array.from(row.querySelectorAll('td')).map((td) => td.getAttribute('data-label'));
    expect(labels).toEqual(['Time', 'Strategy', 'View', 'Contract', 'Side', 'Qty', 'Entry', 'Exit', 'P&L', 'R', 'Hold', 'Exit reason']);
    expect(row.querySelector('td.jr-first')).not.toBeNull();
    expect(screen.getByTestId('trades-table')).toHaveClass('jr-rt', 'jr-trades');
  });
});

describe('expanding a trade', () => {
  it('opens the full story and closes it again', async () => {
    renderWithClient(<Harness />);
    const row = await screen.findByTestId('trade-row-SB-1');
    const toggle = within(row).getByRole('button', { name: /Show the story of NIFTY 25000 CE/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('trade-story')).toBeNull();

    fireEvent.click(toggle);
    const story = await screen.findByTestId('trade-story');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', story.id);
    expect(within(story).getByTestId('trade-narrative')).toHaveTextContent('Bought 1 lot NIFTY 25000 CE at 120.00');
    expect(within(story).getByText('Why it entered')).toBeInTheDocument();
    expect(within(story).getByText('Move being faded')).toBeInTheDocument();
    expect(within(story).getByText('A down-move')).toBeInTheDocument();
    expect(paramsOf(get, '/trades/SB-1')).toEqual({ mode: 'simulation' });

    fireEvent.click(within(row).getByRole('button', { name: /Hide the story/ }));
    expect(screen.queryByTestId('trade-story')).toBeNull();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('lists the timeline in order with times: signal, order, fill, stop, trail, exit', async () => {
    renderWithClient(<Harness />);
    fireEvent.click(within(await screen.findByTestId('trade-row-SB-1')).getByRole('button', { name: /Show the story/ }));
    const timeline = await screen.findByTestId('trade-timeline');
    const steps = within(timeline).getAllByRole('listitem');
    expect(steps.map((s) => s.getAttribute('data-step'))).toEqual([
      'SIGNAL', 'ORDER_SUBMITTED', 'FILLED', 'PROTECTION_PLACED', 'TSL_MOVED', 'EXIT_FILLED',
    ]);
    expect(steps[0]).toHaveTextContent('09:29:00');
    expect(steps[0]).toHaveTextContent('Signal seen');
    expect(steps[2]).toHaveTextContent('Order filled');
    expect(steps[2]).toHaveTextContent('at 120.00');
    expect(steps[3]).toHaveTextContent('Safety stop placed');
    expect(steps[4]).toHaveTextContent('Stop moved');
    expect(steps[4]).toHaveTextContent('Stop raised to 118.00');
    expect(steps[5]).toHaveTextContent('Exit filled');
    expect(steps[5]).toHaveTextContent('+₹2,250.00');
  });

  it('gives the numbers with gross against net and says when charges are estimated', async () => {
    renderWithClient(<Harness />);
    fireEvent.click(within(await screen.findByTestId('trade-row-SB-1')).getByRole('button', { name: /Show the story/ }));
    const story = await screen.findByTestId('trade-story');
    const text = story.textContent ?? '';
    expect(text).toContain('Gross P&L (before charges)');
    expect(text).toContain('+₹2,250.00');
    expect(text).toContain('Charges and taxes (estimated)');
    expect(text).toContain('₹62.50');
    expect(text).toContain('Net P&L (what you keep)');
    expect(text).toContain('+₹2,187.50');
    expect(text).toContain('SL (safety limit at the start)');
    expect(text).toContain('TSL (trailing stop)');
    expect(text).toContain('118.00 after 1 move');
    expect(text).toContain('Risk (most it planned to lose)');
    expect(text).toContain('₹1,800.00');
    expect(text).toMatch(/How it ended: Target reached\./);
  });

  it('adds a "What went wrong" line for a loss only', async () => {
    renderWithClient(<Harness />);
    fireEvent.click(within(await screen.findByTestId('trade-row-GU-1')).getByRole('button', { name: /Show the story/ }));
    const wrong = await screen.findByTestId('what-went-wrong');
    expect(wrong).toHaveTextContent('What went wrong.');
    expect(wrong).toHaveTextContent('fell to its stop-loss');
    expect(wrong).toHaveTextContent('went wrong fast (7m after entry)');
    expect(wrong).toHaveTextContent('warnings: thin book');
    const story = screen.getByTestId('trade-story');
    expect(within(story).getByText('Open interest')).toBeInTheDocument();
    expect(within(story).getByText('fell 6.2%')).toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId('trade-row-SB-1')).getByRole('button', { name: /Show the story/ }));
    await waitFor(() => expect(screen.getAllByTestId('trade-story')).toHaveLength(2));
    expect(screen.getAllByTestId('what-went-wrong')).toHaveLength(1);
  });

  it('shows the story from the list row at once, and a note when the timeline cannot load', async () => {
    routeGet(get, { '/trades/SB-1': new Error('HTTP 500') });
    renderWithClient(<Harness />);
    fireEvent.click(within(await screen.findByTestId('trade-row-SB-1')).getByRole('button', { name: /Show the story/ }));
    expect(screen.getByTestId('trade-narrative')).toHaveTextContent('Bought 1 lot');
    expect(await screen.findByText(/Could not load the timeline\. HTTP 500/)).toBeInTheDocument();

    routeGet(get);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('trade-timeline')).toBeInTheDocument();
  });

  it('says so when no events were recorded', async () => {
    routeGet(get, { '/trades/ST-1': { mode: 'SIMULATION', trade: { ...OPEN, timeline: [] } } });
    renderWithClient(<Harness />);
    fireEvent.click(within(await screen.findByTestId('trade-row-ST-1')).getByRole('button', { name: /Show the story/ }));
    expect(await screen.findByText('No step-by-step events were recorded for this trade.')).toBeInTheDocument();
    expect(screen.queryByTestId('what-went-wrong')).toBeNull();
  });

  it('works out the risk and the number list from the trade alone', () => {
    expect(plannedRisk(WIN)).toBe(1800);
    expect(plannedRisk(trade({ initial_sl: null }))).toBeNull();
    const rows = numbersFor(OPEN).map((r) => r.label);
    expect(rows).toContain('Entry');
    expect(rows).not.toContain('Gross P&L (before charges)');
    expect(numbersFor(LOSS).map((r) => r.label)).toContain('Highest it was ahead');
  });
});

describe('filters become request parameters', () => {
  it('sends the outcome, exit reason, sort and symbol, and starts again from page one', async () => {
    renderWithClient(<Harness initial={sim({ mode: 'all', strategy: 'gamma' })} />);
    await screen.findByTestId('trades-table');
    expect(paramsOf(get, '/trades')).toEqual({
      mode: 'all', strategy: 'gamma', date_from: '2026-10-01', date_to: '2026-10-07', sort: 'newest', limit: '25', offset: '0',
    });

    fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'loss' } });
    await waitFor(() => expect(paramsOf(get, '/trades').outcome).toBe('loss'));
    fireEvent.change(screen.getByLabelText('Exit reason'), { target: { value: 'SL' } });
    await waitFor(() => expect(paramsOf(get, '/trades').exit_reason).toBe('SL'));
    fireEvent.change(screen.getByLabelText('Sort trades'), { target: { value: 'pnl_asc' } });
    await waitFor(() => expect(paramsOf(get, '/trades').sort).toBe('pnl_asc'));
    fireEvent.change(screen.getByLabelText('Search by symbol'), { target: { value: ' nifty ' } });
    await waitFor(() => expect(paramsOf(get, '/trades').symbol).toBe('nifty'), { timeout: 2000 });
    expect(paramsOf(get, '/trades')).toMatchObject({ outcome: 'loss', exit_reason: 'SL', sort: 'pnl_asc', offset: '0', strategy: 'gamma' });
  });

  it('does not search the server on every keystroke', async () => {
    renderWithClient(<Harness />);
    await screen.findByTestId('trades-table');
    const before = get.mock.calls.length;
    const box = screen.getByLabelText('Search by symbol');
    fireEvent.change(box, { target: { value: 'N' } });
    fireEvent.change(box, { target: { value: 'NI' } });
    fireEvent.change(box, { target: { value: 'NIF' } });
    expect(get.mock.calls.length).toBe(before);
    await waitFor(() => expect(paramsOf(get, '/trades').symbol).toBe('NIF'), { timeout: 2000 });
  });

  it('pages 25 at a time', async () => {
    const many = Array.from({ length: 25 }, (_, i) => trade({ trade_id: `T${i}` }));
    routeGet(get, { '/trades': (q: URLSearchParams) => ({ mode: 'SIMULATION', total: 61, limit: 25, offset: Number(q.get('offset')), items: many }) });
    renderWithClient(<Harness />);
    await screen.findByTestId('trades-table');
    expect(screen.getByTestId('trades-range')).toHaveTextContent('Showing 1–25 of 61');
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(paramsOf(get, '/trades').offset).toBe('25'));
    await waitFor(() => expect(screen.getByTestId('trades-range')).toHaveTextContent('Showing 26–50 of 61'));
    fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'win' } });
    await waitFor(() => expect(paramsOf(get, '/trades')).toMatchObject({ outcome: 'win', offset: '0' }));
  });
});

describe('empty and error states', () => {
  it('teaches in Simulation mode when there are no trades today', async () => {
    routeGet(get, { '/trades': page([]) });
    renderWithClient(<Harness initial={{ ...defaultFilters('simulation'), range: 'today' }} />);
    expect(await screen.findByText('No trades yet today.')).toBeInTheDocument();
    expect(screen.getByText('In Simulation mode the app trades with virtual money as signals appear.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });

  it('offers to clear the filters when they are what hides the trades', async () => {
    routeGet(get, { '/trades': page([]) });
    const reset = vi.fn();
    renderWithClient(<Harness initial={sim({ outcome: 'loss', symbol: '' })} reset={reset} />);
    expect(await screen.findByText('No trades match these filters.')).toBeInTheDocument();
    routeGet(get);
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(reset).toHaveBeenCalled();
    expect(await screen.findByTestId('trades-table')).toBeInTheDocument();
    expect(paramsOf(get, '/trades').outcome).toBeUndefined();
  });

  it('words the empty message for the view and the period', () => {
    const f = defaultFilters('live');
    expect(emptyTradesMessage({ ...f, range: 'today' })).toEqual({
      title: 'No trades yet today.',
      body: 'In Live mode the app trades real money when a strategy is set to ALGO and a signal appears.',
    });
    expect(emptyTradesMessage({ ...f, mode: 'all', range: 'today' }).body).toMatch(/Live and from Simulation/);
    expect(emptyTradesMessage({ ...f, mode: 'simulation', range: '7d' })).toEqual({
      title: 'No trades in this period.',
      body: 'In Simulation mode the app trades with virtual money as signals appear. Try a longer date range to see earlier trades.',
    });
    expect(emptyTradesMessage({ ...f, strategy: 'gamma' }).title).toBe('No trades match these filters.');
  });

  it('shows loading, then an error with a working retry', async () => {
    routeGet(get, { '/trades': new Error('{"code":"owner_only","message":"Combined live and simulation view is for the owner only."}') });
    renderWithClient(<Harness />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading the trades');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the trades. Combined live and simulation view is for the owner only.');
    routeGet(get);
    fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('trades-table')).toBeInTheDocument();
  });
});

describe('CSV export', () => {
  it('exports every trade that matches the filters, not just the page on screen', async () => {
    const a = Array.from({ length: 500 }, (_, i) => trade({ trade_id: `A${i}` }));
    const b = Array.from({ length: 20 }, (_, i) => trade({ trade_id: `B${i}`, strategy: 'gamma', strategy_label: 'Gamma Unwind' }));
    routeGet(get, {
      '/trades': (q: URLSearchParams) => (q.get('limit') === '500'
        ? { mode: 'SIMULATION', total: 520, limit: 500, offset: Number(q.get('offset')), items: Number(q.get('offset')) ? b : a }
        : { mode: 'SIMULATION', total: 520, limit: 25, offset: 0, items: a.slice(0, 25) }),
    });
    const download = vi.fn();
    renderWithClient(<Harness initial={sim({ outcome: 'win', strategy: 'snapback' })} download={download} />);
    await screen.findByTestId('trades-table');

    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    const [filename, csv] = download.mock.calls[0] as [string, string];
    expect(filename).toMatch(/^sterling-journal-simulation-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = csv.split('\r\n');
    expect(lines).toHaveLength(521);
    expect(lines[0].startsWith('Date,Time (IST),Mode,Strategy')).toBe(true);
    expect(csv).toContain('Gamma Unwind');
    expect(await screen.findByText('Saved 520 trades.')).toBeInTheDocument();

    const exportCalls = get.mock.calls.map((c) => new URLSearchParams(String(c[0]).split('?')[1])).filter((q) => q.get('limit') === '500');
    expect(exportCalls.map((q) => q.get('offset'))).toEqual(['0', '500']);
    expect(exportCalls[0].get('outcome')).toBe('win');
    expect(exportCalls[0].get('strategy')).toBe('snapback');
  });

  it('says so instead of saving an empty file', async () => {
    routeGet(get, { '/trades': page([]) });
    const download = vi.fn();
    renderWithClient(<Harness download={download} />);
    await screen.findByText('No trades in this period.');
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }));
    expect(await screen.findByText('There are no trades to export for these filters.')).toBeInTheDocument();
    expect(download).not.toHaveBeenCalled();
  });

  it('reports a failed export and lets the person try again', async () => {
    const download = vi.fn();
    renderWithClient(<Harness download={download} />);
    await screen.findByTestId('trades-table');
    routeGet(get, { '/trades': new Error('HTTP 500') });
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }));
    expect(await screen.findByText('Could not export. HTTP 500')).toBeInTheDocument();
    expect(download).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeEnabled();
  });
});

// React import keeps the JSX runtime happy under the test transform.
void React;

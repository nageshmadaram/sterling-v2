import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within, fireEvent, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({ useEngineActivity: () => ({ data: { market_open: true } }) }));

import { CalendarStrip, EquitySection } from '../EquitySection';
import { ExitReasons } from '../ExitReasons';
import { SkippedSignals } from '../SkippedSignals';
import { buildEquitySeries, calendarSentence, curveSummary, dayIntensity, fmtDayLabel, indexAt, toPath, xAt, yAt, yDomain } from '../chartModel';
import { CALENDAR, EQUITY, EXIT_REASONS, SKIPS, T0 } from './fixtures';
import { paramsOf, renderWithClient, routeGet, sim } from './testkit';
import type { EquityResponse } from '../types';

beforeEach(() => { get.mockReset(); routeGet(get); });

describe('chart model', () => {
  it('starts every curve at zero and then follows each closed trade', () => {
    const [s] = buildEquitySeries(EQUITY);
    expect(s.points.map((p) => p.cum)).toEqual([0, 2187.5, 327.5, 1227.5]);
    expect(s.points[0].label).toBe('Start');
    expect(s.label).toBe('Simulation total');
  });

  it('draws one line per view on a shared day axis when Live and Simulation are combined', () => {
    const eq: EquityResponse = {
      mode: 'ALL', by_trade: [], by_day: [],
      by_mode: {
        LIVE: { by_trade: [], by_day: [{ day: '2026-10-06', pnl: 100, trades: 1, cum_pnl: 100 }] },
        SIMULATION: { by_trade: [], by_day: [{ day: '2026-10-06', pnl: 50, trades: 1, cum_pnl: 50 }, { day: '2026-10-07', pnl: -20, trades: 1, cum_pnl: 30 }] },
      },
    };
    const series = buildEquitySeries(eq);
    expect(series.map((s) => s.label)).toEqual(['Live', 'Simulation']);
    expect(series[0].points.map((p) => p.cum)).toEqual([0, 100, 100]); // carried forward on the day with no live trade
    expect(series[1].points.map((p) => p.cum)).toEqual([0, 50, 30]);
  });

  it('has nothing to draw without trades', () => {
    expect(buildEquitySeries(undefined)).toEqual([]);
    expect(buildEquitySeries({ mode: 'LIVE', by_trade: [], by_day: [] })).toEqual([]);
    expect(curveSummary([])).toBe('No profit curve yet.');
  });

  it('keeps zero inside the plot and maps values top-down', () => {
    const [s] = buildEquitySeries(EQUITY);
    const d = yDomain([s]);
    expect(d.min).toBeLessThan(0);
    expect(d.max).toBeGreaterThan(2187.5);
    expect(yAt(d.max, d)).toBeCloseTo(0);
    expect(yAt(d.min, d)).toBeCloseTo(100);
    expect(yAt(0, d)).toBeGreaterThan(0);
    expect(xAt(0, 4)).toBe(0);
    expect(xAt(3, 4)).toBe(100);
    expect(xAt(0, 1)).toBe(50);
    expect(toPath(s.points, d).startsWith('M0.00')).toBe(true);
    expect(toPath(s.points, d).match(/L/g)).toHaveLength(3);
    expect(yDomain([{ key: 'x', label: 'x', color: 'c', points: [{ label: 'a', pnl: null, cum: 0 }] }]).max).toBeGreaterThan(0);
  });

  it('turns a pointer position into a point', () => {
    expect(indexAt(0, 4)).toBe(0);
    expect(indexAt(1, 4)).toBe(3);
    expect(indexAt(0.5, 5)).toBe(2);
    expect(indexAt(9, 4)).toBe(3);
    expect(indexAt(0.4, 1)).toBe(0);
  });

  it('reads a day without time-zone drift', () => {
    expect(fmtDayLabel('2026-10-06')).toBe('Tue 06 Oct');
    expect(calendarSentence(CALENDAR.days[0])).toBe('Tue 06 Oct: +₹327.50 · 2 trades · 1 won, 1 lost');
    expect(dayIntensity(null, 100)).toBe(18);
    expect(dayIntensity(100, 100)).toBe(80);
    expect(dayIntensity(-50, 100)).toBeGreaterThan(dayIntensity(-10, 100));
  });
});

describe('EquitySection', () => {
  it('draws the curve, reads the latest total aloud and lists the days as a table', async () => {
    renderWithClient(<EquitySection filters={sim()} />);
    const line = await screen.findByTestId('equity-line-SIMULATION');
    expect(line.getAttribute('d')).toMatch(/^M/);
    expect(screen.getByTestId('equity-readout')).toHaveTextContent('Simulation total · 07 Oct 09:30: +₹1,227.50');
    expect(screen.getByRole('img', { name: /Simulation total: ₹0\.00 at the start, \+₹1,227\.50 now, over 3 points/ })).toBeInTheDocument();

    const table = within(screen.getByTestId('equity-table'));
    expect(table.getByText('Tue 06 Oct')).toBeInTheDocument();
    expect(table.getByText('+₹900.00')).toBeInTheDocument();
    expect(table.getAllByText('+₹1,227.50').length).toBeGreaterThan(0);
  });

  it('walks the points with the arrow keys and returns to the latest on Escape', async () => {
    renderWithClient(<EquitySection filters={sim()} />);
    const plot = await screen.findByTestId('equity-plot');
    plot.focus();
    fireEvent.keyDown(plot, { key: 'ArrowLeft' });
    expect(screen.getByTestId('equity-readout')).toHaveTextContent('+₹327.50');
    fireEvent.keyDown(plot, { key: 'Home' });
    expect(screen.getByTestId('equity-readout')).toHaveTextContent('Start: ₹0.00');
    fireEvent.keyDown(plot, { key: 'ArrowRight' });
    expect(screen.getByTestId('equity-readout')).toHaveTextContent('+₹2,187.50');
    fireEvent.keyDown(plot, { key: 'Escape' });
    expect(screen.getByTestId('equity-readout')).toHaveTextContent('+₹1,227.50');
  });

  it('draws a legend line per view when both are combined', async () => {
    routeGet(get, {
      '/equity': {
        mode: 'ALL', by_trade: [], by_day: [],
        by_mode: {
          LIVE: { by_trade: [], by_day: [{ day: '2026-10-06', pnl: 100, trades: 1, cum_pnl: 100 }] },
          SIMULATION: { by_trade: [], by_day: [{ day: '2026-10-06', pnl: -50, trades: 1, cum_pnl: -50 }] },
        },
      },
    });
    renderWithClient(<EquitySection filters={sim({ mode: 'all' })} />);
    expect(await screen.findByTestId('equity-line-LIVE')).toBeInTheDocument();
    expect(screen.getByTestId('equity-line-SIMULATION')).toBeInTheDocument();
    const readout = screen.getByTestId('equity-readout');
    expect(readout).toHaveTextContent('Live');
    expect(readout).toHaveTextContent('Simulation');
    expect(paramsOf(get, '/equity').mode).toBe('all');
  });

  it('colours each day of the strip and reads it out on hover or focus', async () => {
    renderWithClient(<EquitySection filters={sim()} />);
    const strip = await screen.findByTestId('calendar-strip');
    const win = within(strip).getByTestId('day-2026-10-06');
    const loss = within(strip).getByTestId('day-2026-10-07');
    expect(win).toHaveAttribute('aria-label', 'Tue 06 Oct: +₹327.50 · 2 trades · 1 won, 1 lost');
    expect(win.style.background).toContain('var(--k-green)');
    expect(loss.style.background).toContain('var(--k-red)');
    expect(screen.getByTestId('calendar-readout')).toHaveTextContent('Wed 07 Oct: -₹900.00');
    fireEvent.focus(win);
    expect(screen.getByTestId('calendar-readout')).toHaveTextContent('Tue 06 Oct: +₹327.50');
  });

  it('teaches when no trade has closed yet', async () => {
    routeGet(get, { '/equity': { mode: 'SIMULATION', by_trade: [], by_day: [] }, '/calendar': { mode: 'SIMULATION', days: [] } });
    renderWithClient(<EquitySection filters={sim()} />);
    expect(await screen.findByText('No closed trades to draw yet')).toBeInTheDocument();
    expect(await screen.findByText('No day has a closed trade yet.')).toBeInTheDocument();
  });

  it('reports a failed curve without hiding the daily strip', async () => {
    routeGet(get, { '/equity': new Error('HTTP 503') });
    renderWithClient(<EquitySection filters={sim()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the profit curve. HTTP 503');
    expect(await screen.findByTestId('calendar-strip')).toBeInTheDocument();
  });

  it('is a standalone strip too', () => {
    renderWithClient(<CalendarStrip days={[{ ...CALENDAR.days[0], net_pnl: 0 }]} />);
    expect(screen.getByTestId('calendar-readout')).toHaveTextContent('₹0.00');
    expect(T0).toBeGreaterThan(0);
  });
});

describe('ExitReasons', () => {
  it('counts and prices each way a trade ended, each with a one-line explanation', async () => {
    renderWithClient(<ExitReasons filters={sim()} />);
    const target = await screen.findByTestId('exit-TARGET');
    expect(within(target).getByText('Target reached')).toBeInTheDocument();
    expect(within(target).getByText(/reached the profit goal set at the start/)).toBeInTheDocument();
    expect(within(target).getByText('3 / 0')).toBeInTheDocument();
    expect(within(target).getByText('+₹4,500.00')).toBeInTheDocument();
    expect(within(target).getByText('+₹1,500.00')).toBeInTheDocument();

    const sl = screen.getByTestId('exit-SL');
    expect(within(sl).getByText(/SL is the stop-loss: a safety limit/)).toBeInTheDocument();
    expect(within(sl).getByText('-₹1,860.00')).toBeInTheDocument();
    expect(within(sl).getByText('0 / 2')).toBeInTheDocument();
    expect(paramsOf(get, '/exit-reasons')).toMatchObject({ mode: 'simulation', date_from: '2026-10-01' });
    expect(EXIT_REASONS.reasons).toHaveLength(2);
  });

  it('keeps a glossary of every reason, including ones that have not happened yet', async () => {
    renderWithClient(<ExitReasons filters={sim()} />);
    const glossary = await screen.findByTestId('exit-glossary');
    for (const label of ['Stop-loss hit', 'Trailing stop hit', 'Target reached', 'End-of-day exit', 'Time stop', 'Square-off']) {
      expect(within(glossary).getByText(label)).toBeInTheDocument();
    }
    expect(within(glossary).getByText(/Trades that are not meant to stay overnight/)).toBeInTheDocument();
  });

  it('teaches when nothing has closed, and reports errors', async () => {
    routeGet(get, { '/exit-reasons': { mode: 'SIMULATION', reasons: [] } });
    const first = renderWithClient(<ExitReasons filters={sim()} />);
    expect(await screen.findByText('No finished trades yet')).toBeInTheDocument();
    first.unmount();

    routeGet(get, { '/exit-reasons': new Error('HTTP 500') });
    renderWithClient(<ExitReasons filters={sim()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the exit reasons');
  });
});

describe('SkippedSignals', () => {
  it('lists each declined signal with its plain-English reason and how often it was seen', async () => {
    renderWithClient(<SkippedSignals filters={sim()} />);
    const list = await screen.findByTestId('skip-list');
    const first = within(list).getByTestId('skip-1');
    expect(within(first).getByText('Gamma Unwind')).toBeInTheDocument();
    expect(within(first).getByText('NIFTY')).toBeInTheDocument();
    expect(within(first).getByText('seen 3 times')).toBeInTheDocument();
    expect(within(first).getByText('The daily loss limit is reached; new entries are halted for today.')).toBeInTheDocument();
    expect(within(screen.getByTestId('skip-2')).queryByText(/seen/)).toBeNull();
    expect(within(screen.getByTestId('skip-reasons')).getByText(/Trend strength \(ADX\) was below the minimum\./)).toBeInTheDocument();
    expect(paramsOf(get, '/skips')).toMatchObject({ mode: 'simulation', limit: '15', offset: '0' });
  });

  it('pages through more skips', async () => {
    routeGet(get, { '/skips': (q: URLSearchParams) => ({ ...SKIPS, total: 20, offset: Number(q.get('offset')) }) });
    renderWithClient(<SkippedSignals filters={sim()} />);
    await screen.findByTestId('skip-list');
    expect(screen.getByText('Showing 1–2 of 20')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(paramsOf(get, '/skips').offset).toBe('15'));
  });

  it('explains what the list is for when nothing was skipped', async () => {
    routeGet(get, { '/skips': { ...SKIPS, total: 0, items: [], by_code: [] } });
    renderWithClient(<SkippedSignals filters={sim()} />);
    expect(await screen.findByText('No skipped signals in this period')).toBeInTheDocument();
    expect(screen.getByText(/the signal and the plain-English reason appear here/)).toBeInTheDocument();
  });

  it('shows an error with a retry', async () => {
    routeGet(get, { '/skips': new Error('HTTP 500') });
    renderWithClient(<SkippedSignals filters={sim()} />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the skipped signals. HTTP 500');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

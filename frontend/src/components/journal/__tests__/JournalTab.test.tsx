import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, within, fireEvent, waitFor, act } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({ useEngineActivity: () => ({ data: { market_open: true } }) }));

import { JournalTab } from '../JournalTab';
import { addDays, istDay } from '../journalModel';
import { useViewStore } from '../../../store/useViewStore';
import { SCOPE, summary } from './fixtures';
import { paramsOf, renderWithClient, routeGet } from './testkit';

beforeEach(() => { get.mockReset(); routeGet(get); useViewStore.setState({ view: 'live' }); });
afterEach(() => useViewStore.setState({ view: 'live' }));

describe('Journal tab', () => {
  it('shows every section, in reading order', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    const order = ['journal-summary', 'journal-strategies', 'journal-equity', 'journal-trades', 'journal-exit-reasons', 'journal-skips'];
    const found = Array.from(document.querySelectorAll('[data-testid^="journal-"]')).map((e) => e.getAttribute('data-testid'));
    expect(order.filter((id) => found.includes(id))).toEqual(order);
    expect(found.indexOf('journal-summary')).toBeLessThan(found.indexOf('journal-skips'));
    expect(screen.getByRole('heading', { level: 1, name: 'Trade Journal' })).toBeInTheDocument();
    expect(screen.getByTestId('journal-updated')).toHaveTextContent('refreshes every 30 s while the market is open');
  });

  it('opens on the app’s current view', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    expect(screen.getByRole('button', { name: 'Live', pressed: true })).toBeInTheDocument();
    expect(paramsOf(get, '/summary').mode).toBe('live');

    get.mockClear();
    routeGet(get);
    act(() => useViewStore.getState().setView('simulation'));
    await waitFor(() => expect(paramsOf(get, '/summary').mode).toBe('simulation'));
    expect(screen.getByRole('button', { name: 'Simulation', pressed: true })).toBeInTheDocument();
    expect(screen.getByTestId('mode-note')).toHaveTextContent('virtual money. No real orders are sent');
  });

  it('opens on Simulation when the app is in the Simulation view', async () => {
    useViewStore.setState({ view: 'simulation' });
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    expect(paramsOf(get, '/summary').mode).toBe('simulation');
    expect(paramsOf(get, '/trades').mode).toBe('simulation');
    expect(paramsOf(get, '/skips').mode).toBe('simulation');
  });

  it('offers the three views to the owner and sends the one picked to every section', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    const group = screen.getByRole('group', { name: 'Which trades' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Live', 'Simulation', 'All']);

    fireEvent.click(within(group).getByRole('button', { name: 'All' }));
    await waitFor(() => expect(paramsOf(get, '/trades').mode).toBe('all'));
    for (const path of ['/summary', '/by-strategy', '/equity', '/calendar', '/exit-reasons', '/skips']) {
      await waitFor(() => expect(paramsOf(get, path).mode).toBe('all'));
    }
    expect(screen.getByTestId('mode-note')).toHaveTextContent('added together. The totals mix the two');
  });

  it('offers only Live to someone who may not read the rest', async () => {
    routeGet(get, { '/scope': { ...SCOPE, modes: ['live'], can_view_all: false } });
    useViewStore.setState({ view: 'simulation' });
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    const group = screen.getByRole('group', { name: 'Which trades' });
    await waitFor(() => expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Live']));
    await waitFor(() => expect(paramsOf(get, '/summary').mode).toBe('live'));
  });

  it('turns the time period into dates, and custom dates into the request', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    const today = istDay();
    expect(paramsOf(get, '/summary')).toMatchObject({ date_from: addDays(today, -29), date_to: today });

    const period = screen.getByRole('group', { name: 'Time period' });
    fireEvent.click(within(period).getByRole('button', { name: 'Today' }));
    await waitFor(() => expect(paramsOf(get, '/summary')).toMatchObject({ date_from: today, date_to: today }));
    fireEvent.click(within(period).getByRole('button', { name: 'All time' }));
    await waitFor(() => expect(paramsOf(get, '/summary').date_from).toBeUndefined());

    fireEvent.click(within(period).getByRole('button', { name: 'Pick dates' }));
    fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-01' } });
    fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-09-30' } });
    await waitFor(() => expect(paramsOf(get, '/trades')).toMatchObject({ date_from: '2026-09-01', date_to: '2026-09-30' }));
  });

  it('narrows the numbers to one strategy but keeps the comparison across all three', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    fireEvent.change(screen.getByLabelText('Strategy'), { target: { value: 'snapback' } });
    await waitFor(() => expect(paramsOf(get, '/summary').strategy).toBe('snapback'));
    await waitFor(() => expect(paramsOf(get, '/trades').strategy).toBe('snapback'));
    expect(paramsOf(get, '/by-strategy').strategy).toBeUndefined();
  });

  it('replaces the numbers with a teaching message when nothing has traded, but still lists skipped signals', async () => {
    routeGet(get, {
      '/summary': { mode: 'SIMULATION', summary: summary({ trades: 0, open: 0, pending: 0, wins: 0, losses: 0, net_pnl: 0 }) },
    });
    useViewStore.setState({ view: 'simulation' });
    renderWithClient(<JournalTab />);
    fireEvent.click(within(screen.getByRole('group', { name: 'Time period' })).getByRole('button', { name: 'Today' }));
    expect(await screen.findByText('No trades yet today.')).toBeInTheDocument();
    expect(screen.getByText('In Simulation mode the app trades with virtual money as signals appear.')).toBeInTheDocument();
    expect(screen.getByText('What this page will show once trades happen')).toBeInTheDocument();
    expect(screen.queryByTestId('summary-cards')).toBeNull();
    expect(screen.queryByTestId('journal-trades')).toBeNull();
    expect(await screen.findByTestId('skip-list')).toBeInTheDocument();
  });

  it('refreshes on demand', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    const before = get.mock.calls.filter((c) => String(c[0]).includes('/summary')).length;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(get.mock.calls.filter((c) => String(c[0]).includes('/summary')).length).toBeGreaterThan(before));
  });

  it('shows the time of the last update in India time', async () => {
    renderWithClient(<JournalTab />);
    await screen.findByTestId('summary-cards');
    await waitFor(() => expect(screen.getByTestId('journal-updated')).toHaveTextContent(/Updated \d{2}:\d{2}:\d{2} IST/));
  });
});

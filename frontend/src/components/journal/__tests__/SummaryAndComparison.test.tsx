import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({ useEngineActivity: () => ({ data: { market_open: true } }) }));

import { SummaryCards } from '../SummaryCards';
import { StrategyComparison, bestExit } from '../StrategyComparison';
import { BY_STRATEGY, SUMMARY, summary } from './fixtures';
import { paramsOf, renderWithClient, routeGet, sim } from './testkit';

beforeEach(() => { get.mockReset(); routeGet(get); });

describe('SummaryCards', () => {
  it('shows each headline number with the shared formatters and a plain-English line', async () => {
    renderWithClient(<SummaryCards filters={sim()} />);
    const net = await screen.findByTestId('stat-net');
    expect(within(net).getByText('+₹2,360.00')).toBeInTheDocument();
    expect(within(net).getByText('Before charges +₹2,600.00')).toBeInTheDocument();
    expect(net).toHaveAttribute('title', expect.stringMatching(/after every cost/));

    expect(within(screen.getByTestId('stat-winrate')).getByText('75%')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-winrate')).getByText('3 won · 1 lost')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-trades')).getByText('4')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-trades')).getByText('1 still open')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-pf')).getByText('2.42')).toBeInTheDocument();

    const avg = screen.getByTestId('stat-avg');
    expect(within(avg).getByText('+₹1,500.50')).toBeInTheDocument();
    expect(within(avg).getByText('-₹1,860.00')).toBeInTheDocument();

    const bw = screen.getByTestId('stat-bestworst');
    expect(within(bw).getByText('+₹2,187.50')).toBeInTheDocument();
    expect(within(bw).getByText('-₹1,860.00')).toBeInTheDocument();

    expect(within(screen.getByTestId('stat-drawdown')).getByText('₹1,860.00')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-hold')).getByText('1h 35m')).toBeInTheDocument();
    for (const id of ['net', 'winrate', 'trades', 'pf', 'avg', 'bestworst', 'drawdown', 'hold']) {
      expect(screen.getByTestId(`stat-${id}`)).toHaveAttribute('title');
    }
  });

  it('asks for the view, strategy and dates it was given', async () => {
    renderWithClient(<SummaryCards filters={sim({ strategy: 'gamma' })} />);
    await screen.findByTestId('summary-cards');
    expect(paramsOf(get, '/summary')).toEqual({ mode: 'simulation', strategy: 'gamma', date_from: '2026-10-01', date_to: '2026-10-07' });
  });

  it('says "No losses yet" instead of an infinite profit factor, and dashes what is unknown', async () => {
    routeGet(get, { '/summary': { mode: 'SIMULATION', summary: summary({ profit_factor: null, losses: 0, wins: 2, avg_hold_minutes: null, win_rate_pct: null }) } });
    renderWithClient(<SummaryCards filters={sim()} />);
    expect(await screen.findByText('No losses yet')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-hold')).getByText('—')).toBeInTheDocument();
    expect(within(screen.getByTestId('stat-winrate')).getByText('—')).toBeInTheDocument();
  });

  it('shows a loading state, then an error with a working retry', async () => {
    routeGet(get, { '/summary': new Error('{"code":"owner_only","message":"Simulation is available to the owner only."}') });
    renderWithClient(<SummaryCards filters={sim()} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading the summary');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the summary. Simulation is available to the owner only.');
    expect(alert).not.toHaveTextContent('owner_only');

    routeGet(get);
    fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByTestId('summary-cards')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(SUMMARY.summary.trades).toBe(4);
  });
});

describe('StrategyComparison', () => {
  it('names the leader in a plain sentence and lists all three strategies in a fixed order', async () => {
    renderWithClient(<StrategyComparison filters={sim()} />);
    expect(await screen.findByTestId('comparison-sentence')).toHaveTextContent(
      'Snapback is ahead by ₹4,260.00; Gamma Unwind lost money on stop-outs. Supertrend has not closed a trade yet.',
    );
    const rows = within(screen.getByTestId('comparison-table')).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual(['strategy-supertrend', 'strategy-snapback', 'strategy-gamma']);
  });

  it('highlights only the leader and fills trades, win rate, net P&L, profit factor and expectancy', async () => {
    renderWithClient(<StrategyComparison filters={sim()} />);
    const lead = await screen.findByTestId('strategy-snapback');
    expect(lead).toHaveAttribute('data-leader', 'true');
    expect(within(lead).getByText('Leading')).toBeInTheDocument();
    expect(within(lead).getByText('100%')).toBeInTheDocument();
    expect(within(lead).getByText('+₹2,400.00')).toBeInTheDocument();
    expect(within(lead).getByText('No losses')).toBeInTheDocument();
    expect(within(lead).getByText('+₹800.00')).toBeInTheDocument();

    expect(screen.getByTestId('strategy-gamma')).toHaveAttribute('data-leader', 'false');
    expect(within(screen.getByTestId('strategy-gamma')).getByText('-₹1,860.00')).toBeInTheDocument();
    expect(within(screen.getByTestId('strategy-supertrend')).getAllByText('—').length).toBeGreaterThanOrEqual(4);
  });

  it('names each strategy’s best way out from its own exit reasons', async () => {
    routeGet(get, {
      '/exit-reasons': (q: URLSearchParams) => ({
        mode: 'SIMULATION',
        reasons: q.get('strategy') === 'snapback'
          ? [{ reason: 'TSL', label: 'Trailing stop hit', count: 2, pct_of_trades: 66, wins: 2, losses: 0, net_pnl: 900, avg_pnl: 450 },
            { reason: 'TARGET', label: 'Target reached', count: 1, pct_of_trades: 33, wins: 1, losses: 0, net_pnl: 1500, avg_pnl: 1500 }]
          : [{ reason: 'SL', label: 'Stop-loss hit', count: 2, pct_of_trades: 100, wins: 0, losses: 2, net_pnl: -1860, avg_pnl: -930 }],
      }),
    });
    renderWithClient(<StrategyComparison filters={sim()} />);
    await waitFor(() => expect(within(screen.getByTestId('strategy-snapback')).getByText('Target reached (+₹1,500.00)')).toBeInTheDocument());
    // A strategy whose exits all lost money has no best way out.
    expect(within(screen.getByTestId('strategy-gamma')).queryByText(/Stop-loss hit/)).toBeNull();
    expect(bestExit([])).toBeNull();
    expect(bestExit(undefined)).toBeNull();
  });

  it('leaves the strategy filter off the comparison request but keeps view and dates', async () => {
    renderWithClient(<StrategyComparison filters={sim({ strategy: 'gamma' })} />);
    await screen.findByTestId('comparison-table');
    expect(paramsOf(get, '/by-strategy')).toEqual({ mode: 'simulation', date_from: '2026-10-01', date_to: '2026-10-07' });
  });

  it('labels every cell so the table can reflow into cards on a narrow screen', async () => {
    renderWithClient(<StrategyComparison filters={sim()} />);
    const row = await screen.findByTestId('strategy-snapback');
    const labels = Array.from(row.querySelectorAll('td')).map((td) => td.getAttribute('data-label'));
    expect(labels).toEqual(['Strategy', 'Trades', 'Win rate', 'Net P&L', 'Profit factor', 'Average per trade', 'Best way out']);
    expect(screen.getByTestId('comparison-table')).toHaveClass('jr-rt');
  });

  it('shows an error with a retry when the comparison cannot load', async () => {
    routeGet(get, { '/by-strategy': new Error('HTTP 500') });
    renderWithClient(<StrategyComparison filters={sim()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the strategy comparison. HTTP 500');
    expect(BY_STRATEGY.strategies).toHaveLength(3);
  });
});

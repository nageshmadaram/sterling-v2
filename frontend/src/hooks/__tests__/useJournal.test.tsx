import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const get = vi.fn();
vi.mock('../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a) } }));

let marketOpen: boolean | undefined = true;
vi.mock('../useSterlingKiteEngine', () => ({
  useEngineActivity: () => ({ data: marketOpen === undefined ? undefined : { market_open: marketOpen } }),
}));

import {
  JOURNAL_POLL_MS, fetchAllTrades, useCurrentView, useJournalPollMs, useJournalStrategyExitReasons,
  useJournalSummary, useJournalTrade, useJournalTrades,
} from '../useJournal';
import { useViewStore } from '../../store/useViewStore';
import { defaultFilters, type JournalFilters } from '../../components/journal/journalModel';
import { SUMMARY, trade } from '../../components/journal/__tests__/fixtures';

const filters = (over: Partial<JournalFilters> = {}): JournalFilters => ({
  ...defaultFilters('simulation'), range: 'custom', dateFrom: '2026-10-01', dateTo: '2026-10-07', ...over,
});

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  get.mockReset();
  marketOpen = true;
  setVisibility('visible');
});
afterEach(() => setVisibility('visible'));

describe('journal requests', () => {
  it('sends the filters as query parameters, leaving empty ones out', async () => {
    get.mockResolvedValue(SUMMARY);
    const { result } = renderHook(() => useJournalSummary(filters({ strategy: 'gamma' })), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(get).toHaveBeenCalledWith(
      '/api/v1/journal/summary?mode=simulation&strategy=gamma&date_from=2026-10-01&date_to=2026-10-07',
    );
  });

  it('adds the trade-only filters, sort and paging to the trades request', async () => {
    get.mockResolvedValue({ mode: 'ALL', total: 0, limit: 25, offset: 50, items: [] });
    const f = filters({ mode: 'all', outcome: 'loss', symbol: 'NIFTY', exitReason: 'SL', sort: 'pnl_asc' });
    renderHook(() => useJournalTrades(f, 2), { wrapper: wrapper() });
    await waitFor(() => expect(get).toHaveBeenCalled());
    const url = String(get.mock.calls[0][0]);
    expect(url.startsWith('/api/v1/journal/trades?')).toBe(true);
    const q = new URLSearchParams(url.split('?')[1]);
    expect(Object.fromEntries(q)).toEqual({
      mode: 'all', date_from: '2026-10-01', date_to: '2026-10-07', outcome: 'loss', symbol: 'NIFTY',
      exit_reason: 'SL', sort: 'pnl_asc', limit: '25', offset: '50',
    });
  });

  it('fetches one trade by id only once it is asked for', async () => {
    get.mockResolvedValue({ mode: 'SIMULATION', trade: trade() });
    const { rerender } = renderHook(({ on }) => useJournalTrade('SB 1/x', 'simulation', on), {
      wrapper: wrapper(), initialProps: { on: false },
    });
    expect(get).not.toHaveBeenCalled();
    rerender({ on: true });
    await waitFor(() => expect(get).toHaveBeenCalledWith('/api/v1/journal/trades/SB%201%2Fx?mode=simulation'));
  });

  it('asks each strategy for its own exit reasons', async () => {
    get.mockImplementation(async (url: string) => ({
      mode: 'SIMULATION',
      reasons: [{ reason: url.includes('strategy=gamma') ? 'SL' : 'TARGET', label: 'x', count: 1, pct_of_trades: 100, wins: 0, losses: 0, net_pnl: 0, avg_pnl: 0 }],
    }));
    const { result } = renderHook(() => useJournalStrategyExitReasons(filters({ strategy: 'snapback' })), { wrapper: wrapper() });
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(5));
    const urls = get.mock.calls.map((c) => String(c[0]));
    for (const s of ['supertrend', 'snapback', 'gamma', 'ema_trend', 'price_rules']) expect(urls.some((u) => u.includes(`strategy=${s}`))).toBe(true);
    expect(result.current.gamma?.[0].reason).toBe('SL');
    expect(result.current.supertrend?.[0].reason).toBe('TARGET');
  });

  it('never shows the previous view under a new one, but keeps data while filters change inside a view', async () => {
    let release: (v: unknown) => void = () => {};
    get.mockResolvedValueOnce({ ...SUMMARY, mode: 'LIVE' });
    const { result, rerender } = renderHook(({ f }) => useJournalSummary(f), {
      wrapper: wrapper(), initialProps: { f: filters({ mode: 'live' }) },
    });
    await waitFor(() => expect(result.current.data?.mode).toBe('LIVE'));

    get.mockReturnValueOnce(new Promise((r) => { release = r; }));
    rerender({ f: filters({ mode: 'live', strategy: 'gamma' }) });
    expect(result.current.data?.mode).toBe('LIVE'); // same view: the old figures stay while the new load

    get.mockReturnValueOnce(new Promise(() => {}));
    rerender({ f: filters({ mode: 'simulation' }) });
    expect(result.current.data).toBeUndefined(); // another view: no stale numbers under the new label
    release(SUMMARY);
  });
});

describe('auto-refresh', () => {
  it('polls every 30 seconds while the tab is visible and the market is open', () => {
    const { result } = renderHook(() => useJournalPollMs());
    expect(result.current).toBe(JOURNAL_POLL_MS);
    expect(JOURNAL_POLL_MS).toBe(30_000);
  });

  it('stops while the market is closed and resumes when it opens', () => {
    marketOpen = false;
    const { result, rerender } = renderHook(() => useJournalPollMs());
    expect(result.current).toBe(false);
    marketOpen = true;
    rerender();
    expect(result.current).toBe(30_000);
  });

  it('treats an unknown market state as open', () => {
    marketOpen = undefined;
    expect(renderHook(() => useJournalPollMs()).result.current).toBe(30_000);
  });

  it('stops while the page is hidden and resumes when it is shown again', () => {
    const { result } = renderHook(() => useJournalPollMs());
    act(() => setVisibility('hidden'));
    expect(result.current).toBe(false);
    act(() => setVisibility('visible'));
    expect(result.current).toBe(30_000);
  });
});

describe('export', () => {
  const many = (n: number) => Array.from({ length: n }, (_, i) => trade({ trade_id: `T${i}` }));

  it('pages through every matching trade, 500 at a time', async () => {
    get.mockResolvedValueOnce({ mode: 'SIMULATION', total: 620, limit: 500, offset: 0, items: many(500) });
    get.mockResolvedValueOnce({ mode: 'SIMULATION', total: 620, limit: 500, offset: 500, items: many(120) });
    const out = await fetchAllTrades(filters({ outcome: 'win' }));
    expect(out.trades).toHaveLength(620);
    expect(out.truncated).toBe(false);
    const q = (i: number) => Object.fromEntries(new URLSearchParams(String(get.mock.calls[i][0]).split('?')[1]));
    expect(q(0)).toMatchObject({ limit: '500', offset: '0', outcome: 'win', mode: 'simulation' });
    expect(q(1)).toMatchObject({ limit: '500', offset: '500', outcome: 'win' });
  });

  it('stops on an empty page rather than looping', async () => {
    get.mockResolvedValue({ mode: 'LIVE', total: 10, limit: 500, offset: 0, items: [] });
    const out = await fetchAllTrades(filters());
    expect(get).toHaveBeenCalledTimes(1);
    expect(out).toMatchObject({ trades: [], total: 10, truncated: true });
  });
});

describe('current view', () => {
  afterEach(() => { act(() => useViewStore.getState().setView('live')); });

  it('follows the header view switch', () => {
    const { result } = renderHook(() => useCurrentView());
    expect(result.current).toBe('live');
    act(() => useViewStore.getState().setView('simulation'));
    expect(result.current).toBe('simulation');
  });
});

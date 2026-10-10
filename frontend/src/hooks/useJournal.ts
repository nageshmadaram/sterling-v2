/**
 * Data hooks for the Journal tab (/api/v1/journal/*).
 *
 * Auto-refresh: every 30 s, but only while the page is visible and the market is open. A closed
 * market changes nothing, and a hidden tab should not keep the server busy. Switching the view
 * (Live / Simulation / All) never shows the previous view's numbers under the new label: the
 * "keep previous data" smoothing applies only within the same view.
 */
import { useEffect, useState } from 'react';
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useEngineActivity } from './useSterlingKiteEngine';
import { useView, type AppView } from '../store/useViewStore';
import {
  EXPORT_MAX_TRADES, EXPORT_PAGE_SIZE, byStrategyParams, skipsParams, statsParams, toQuery, tradesParams,
  type JournalFilters, type Params,
} from '../components/journal/journalModel';
import {
  STRATEGY_IDS,
  type ByStrategyResponse, type CalendarResponse, type EquityResponse, type ExitReasonRow,
  type ExitReasonsResponse, type JournalMode, type JournalScope, type JournalTrade, type SkipsResponse,
  type StrategyId, type SummaryResponse, type TradeDetail, type TradesPage,
} from '../components/journal/types';

export const JOURNAL_PATH = '/api/v1/journal';
export const JOURNAL_KEY = ['journal'] as const;
export const JOURNAL_POLL_MS = 30_000;

function usePageVisible(): boolean {
  const read = () => typeof document === 'undefined' || document.visibilityState !== 'hidden';
  const [visible, setVisible] = useState(read);
  useEffect(() => {
    const on = () => setVisible(read());
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return visible;
}

/** The refetch interval: 30 s while the tab is visible and the market is open, otherwise off. */
export function useJournalPollMs(): number | false {
  const visible = usePageVisible();
  const { data } = useEngineActivity();
  // Unknown (the activity feed has not answered) counts as open: better one extra read than a stale page.
  const marketOpen = data?.market_open !== false;
  return visible && marketOpen ? JOURNAL_POLL_MS : false;
}

function sameView(prevKey: readonly unknown[] | undefined, params: Params): boolean {
  return ((prevKey?.[2] as Params | undefined)?.mode ?? null) === (params.mode ?? null);
}

function useJournalQuery<T>(kind: string, params: Params, opts: { enabled?: boolean; poll?: boolean } = {}) {
  const poll = useJournalPollMs();
  return useQuery<T>({
    queryKey: [...JOURNAL_KEY, kind, params],
    queryFn: () => api.get<T>(`${JOURNAL_PATH}/${kind}${toQuery(params)}`),
    enabled: opts.enabled ?? true,
    refetchInterval: opts.poll === false ? false : poll,
    refetchOnWindowFocus: true,
    placeholderData: (prev, prevQuery) => (sameView(prevQuery?.queryKey, params) ? prev : undefined),
  });
}

/** Which views the caller may read (the owner sees all three, anyone else only Live). */
export function useJournalScope() {
  return useQuery<JournalScope>({
    queryKey: [...JOURNAL_KEY, 'scope'],
    queryFn: () => api.get<JournalScope>(`${JOURNAL_PATH}/scope`),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export const useJournalSummary = (f: JournalFilters) =>
  useJournalQuery<SummaryResponse>('summary', statsParams(f));

export const useJournalByStrategy = (f: JournalFilters) =>
  useJournalQuery<ByStrategyResponse>('by-strategy', byStrategyParams(f));

export const useJournalExitReasons = (f: JournalFilters) =>
  useJournalQuery<ExitReasonsResponse>('exit-reasons', statsParams(f));

export const useJournalEquity = (f: JournalFilters) =>
  useJournalQuery<EquityResponse>('equity', statsParams(f));

export const useJournalCalendar = (f: JournalFilters) =>
  useJournalQuery<CalendarResponse>('calendar', statsParams(f));

export const useJournalTrades = (f: JournalFilters, page: number) =>
  useJournalQuery<TradesPage>('trades', tradesParams(f, page));

export const useJournalSkips = (f: JournalFilters, page: number) =>
  useJournalQuery<SkipsResponse>('skips', skipsParams(f, page));

/** One trade in full (narrative, entry context, ordered timeline). Fetched only once it is opened. */
export function useJournalTrade(tradeId: string, mode: JournalMode, enabled: boolean) {
  return useJournalQuery<TradeDetail>(`trades/${encodeURIComponent(tradeId)}`, { mode }, { enabled });
}

/**
 * Exit reasons for each strategy on its own, so the comparison can name the one that works best
 * for it. A failed or pending strategy simply reads as unknown in the table.
 */
export function useJournalStrategyExitReasons(f: JournalFilters): Partial<Record<StrategyId, ExitReasonRow[]>> {
  const poll = useJournalPollMs();
  const results = useQueries({
    queries: STRATEGY_IDS.map((strategy) => {
      const params = statsParams({ ...f, strategy });
      return {
        queryKey: [...JOURNAL_KEY, 'exit-reasons', params],
        queryFn: () => api.get<ExitReasonsResponse>(`${JOURNAL_PATH}/exit-reasons${toQuery(params)}`),
        refetchInterval: poll,
        refetchOnWindowFocus: true,
        placeholderData: (prev: ExitReasonsResponse | undefined, prevQuery?: { queryKey: readonly unknown[] }) =>
          (sameView(prevQuery?.queryKey, params) ? prev : undefined),
      };
    }),
  });
  const out: Partial<Record<StrategyId, ExitReasonRow[]>> = {};
  STRATEGY_IDS.forEach((id, i) => {
    const reasons = results[i]?.data?.reasons;
    if (reasons) out[id] = reasons;
  });
  return out;
}

export interface ExportResult { trades: JournalTrade[]; total: number; truncated: boolean }

/**
 * Every trade matching the filters (not just the page on screen), 500 at a time, for the CSV.
 * Bounded at :data:`EXPORT_MAX_TRADES` so a very long history cannot run away; ``truncated`` says so.
 */
export async function fetchAllTrades(f: JournalFilters): Promise<ExportResult> {
  const trades: JournalTrade[] = [];
  let total = 0;
  while (trades.length < EXPORT_MAX_TRADES) {
    const params: Params = { ...tradesParams(f, 0), limit: EXPORT_PAGE_SIZE, offset: trades.length };
    const page = await api.get<TradesPage>(`${JOURNAL_PATH}/trades${toQuery(params)}`);
    total = page.total;
    trades.push(...page.items);
    if (!page.items.length || trades.length >= total) break;
  }
  return { trades, total, truncated: trades.length < total };
}

// ─── The app's current view (Live / Simulation) ─────────────────────────────────────────────────────

/** The header view switch's current book: the Journal opens on it and follows it when it changes. */
export function useCurrentView(): AppView {
  return useView();
}

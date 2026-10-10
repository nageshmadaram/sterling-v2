import React from 'react';
import type { Mock } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BY_STRATEGY, CALENDAR, EQUITY, EXIT_REASONS, LOSS, SCOPE, SKIPS, SUMMARY, TIMELINE, WIN, OPEN, page } from './fixtures';
import { defaultFilters, type JournalFilters } from '../journalModel';
import type { JournalTrade } from '../types';

export type Handler = unknown | ((q: URLSearchParams, url: string) => unknown);

/**
 * Answers every /api/v1/journal/* GET from fixtures. ``overrides`` is keyed by the path after
 * /api/v1/journal (``/summary``, ``/trades``, ``/trades/GU-1`` ...); a function receives the query
 * string so a test can answer by parameter; an Error value is thrown (a failed request).
 */
export function routeGet(get: Mock, overrides: Record<string, Handler> = {}, trades: JournalTrade[] = [WIN, LOSS, OPEN]) {
  const byId = new Map(trades.map((t) => [t.trade_id, t]));
  const defaults: Record<string, Handler> = {
    '/scope': SCOPE,
    '/summary': SUMMARY,
    '/by-strategy': BY_STRATEGY,
    '/exit-reasons': EXIT_REASONS,
    '/equity': EQUITY,
    '/calendar': CALENDAR,
    '/skips': SKIPS,
    '/trades': page(trades),
  };
  get.mockImplementation(async (url: string) => {
    const [full, qs = ''] = url.split('?');
    const path = full.replace('/api/v1/journal', '');
    const q = new URLSearchParams(qs);
    let h: Handler | undefined = overrides[path];
    if (h === undefined && path.startsWith('/trades/')) {
      const id = decodeURIComponent(path.slice('/trades/'.length));
      const t = byId.get(id);
      h = t ? { mode: 'SIMULATION', trade: { ...t, timeline: TIMELINE } } : new Error('{"code":"trade_not_found","message":"No such trade in this view."}');
    }
    if (h === undefined) h = defaults[path];
    if (h === undefined) throw new Error(`unrouted ${url}`);
    const value = typeof h === 'function' ? (h as (q: URLSearchParams, u: string) => unknown)(q, url) : h;
    if (value instanceof Error) throw value;
    return value;
  });
}

export function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

export const sim = (over: Partial<JournalFilters> = {}): JournalFilters => ({
  ...defaultFilters('simulation'), range: 'custom', dateFrom: '2026-10-01', dateTo: '2026-10-07', ...over,
});

/** The query parameters of the n-th (or last) request whose path starts with ``path``. */
export function paramsOf(get: Mock, path: string, which: 'last' | number = 'last'): Record<string, string> {
  const calls = get.mock.calls.map((c) => String(c[0])).filter((u) => u.split('?')[0] === `/api/v1/journal${path}`);
  const url = which === 'last' ? calls[calls.length - 1] : calls[which];
  return Object.fromEntries(new URLSearchParams((url || '').split('?')[1] || ''));
}

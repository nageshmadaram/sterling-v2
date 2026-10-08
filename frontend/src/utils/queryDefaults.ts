import { keepPreviousData, type DefaultOptions } from '@tanstack/react-query';

/**
 * Do not retry what a retry cannot fix. 401 is handled by the API layer (token
 * refresh, then session-expired); 403 and 409 are answers, not outages.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  const e = error as { status?: number; response?: { status?: number } } | null;
  const status = e?.status ?? e?.response?.status;
  if (status === 401 || status === 403 || status === 409) return false;
  return failureCount < 1;
}

export const appQueryDefaults: DefaultOptions = {
  queries: {
    retry: shouldRetryQuery,
    staleTime: 10_000,
    placeholderData: keepPreviousData,
    // Window focus is handled by `useLiveResync`, which refetches only trading
    // state and only after the tab was actually away. Refetching *every* query on
    // focus would re-run heavy ones (candles, instrument search) on each alt-tab.
    refetchOnWindowFocus: false,
    // Regaining network must refetch even data that is still inside its staleTime:
    // a "fresh" result fetched before the outage is exactly what must not be trusted.
    refetchOnReconnect: 'always',
  },
};

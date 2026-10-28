import type { Query, QueryClient } from '@tanstack/react-query';

/**
 * A Kite account change can alter the broker-visible signal, positions,
 * orders and margin inventory without changing Sterling's live/sim view.
 * Never display the previous account's cached board after activation.
 *
 * Keep account-list/status queries separate: they are explicitly updated
 * by the mutation and identify the new selected account.
 */
export function isBrokerAccountScopedQuery(query: Pick<Query, 'queryKey'>): boolean {
  const root = query.queryKey[0];
  if (typeof root !== 'string') return false;
  if (root === 'kite-accounts' || root === 'kite-status') return false;
  return root.startsWith('kite-') ||
    root.startsWith('snapback-') ||
    root.startsWith('gamma-move-') ||
    root === 'ema-trend' || root.startsWith('ema-trend-') ||
    root.startsWith('adaptive-edge');
}

/**
 * resetQueries cancels in-flight results, removes previous cached data and
 * refetches active observers. Crucially, invalidateQueries alone leaves the
 * previous account's data visible until the new response arrives.
 *
 * This refreshes GET queries only. No scan or broker order mutations run.
 */
export function resetBrokerAccountQueries(client: QueryClient): Promise<void> {
  return client.resetQueries({ predicate: isBrokerAccountScopedQuery });
}

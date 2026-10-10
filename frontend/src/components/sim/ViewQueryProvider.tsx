import { useEffect, useRef, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { appQueryDefaults } from '../../utils/queryDefaults';
import { useView, type AppView } from '../../store/useViewStore';
import { useKiteNotifications } from '../../store/useKiteNotifications';

/**
 * One react-query cache per view, and a cold one every time a view is entered.
 *
 * The LIVE and SIMULATION views are served by the same routes (`/kite/positions`, `/kite/orders`,
 * ...) and only the `X-Sterling-View` header tells them apart, so the query key alone cannot.
 * Sharing one cache would paint the other book's rows while a switch refetches, because the app
 * keeps the previous data on screen for a changed key. Instead the provider is keyed by the
 * view, so the whole tree below it remounts onto that view's own client, and the client is
 * emptied when its view is left. The next visit starts from nothing and fetches fresh.
 */
const clients = new Map<AppView, QueryClient>();

export function queryClientFor(view: AppView): QueryClient {
  let client = clients.get(view);
  if (!client) {
    client = new QueryClient({ defaultOptions: appQueryDefaults });
    clients.set(view, client);
  }
  return client;
}

/** Test hook: forget every client. */
export function __resetViewClients(): void {
  clients.forEach((c) => c.clear());
  clients.clear();
}

export function ViewQueryProvider({ children }: { children: ReactNode }) {
  const view = useView();
  const client = queryClientFor(view);

  // Leaving a view (or the whole gate closing) drops what it fetched: requests still in flight
  // land in a cache nobody reads, and nothing stale is waiting on the way back.
  useEffect(() => () => {
    void client.cancelQueries();
    client.clear();
  }, [client]);

  // A toast about a fill in one book must not linger over the other.
  const shown = useRef(view);
  useEffect(() => {
    if (shown.current === view) return;
    shown.current = view;
    useKiteNotifications.getState().clear();
  }, [view]);

  return <QueryClientProvider key={view} client={client}>{children}</QueryClientProvider>;
}

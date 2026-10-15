/**
 * Refetch trading state whenever the screen could have fallen behind the broker.
 *
 * Mounted once at the app root. Triggers: the stream socket reconnecting, a
 * backend restart (boot_id change), the broker feed returning, the browser
 * regaining network, the tab coming back after being hidden, a state-changing
 * write finishing (even a failed one), an account switch, and another tab asking.
 * All of them funnel through one coalescer so a burst becomes a single refetch.
 */
import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  createResyncCoalescer, onPeerMessage, onResync, requestResync, resyncQueries,
} from '../utils/liveSync';

/** Quick alt-tabbing is not worth a refetch; being away this long is. */
export const MIN_AWAY_MS = 5_000;

export function useLiveResync(): void {
  const qc = useQueryClient();

  useEffect(() => {
    const coalescer = createResyncCoalescer(() => { void resyncQueries(qc); });

    const offBus = onResync((reason) => coalescer.request(reason));
    const offPeer = onPeerMessage((m) => {
      if (m.t === 'resync') coalescer.request('remote');
    });

    const onOnline = () => requestResync('online');
    let hiddenAt = typeof document !== 'undefined' && document.visibilityState === 'hidden'
      ? Date.now() : 0;
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now();
        return;
      }
      const away = hiddenAt ? Date.now() - hiddenAt : 0;
      hiddenAt = 0;
      if (away >= MIN_AWAY_MS) requestResync('visible');
    };

    const onPageShow = () => requestResync('visible');
    window.addEventListener('pageshow', onPageShow);
    window.addEventListener('online', onOnline);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      offBus();
      offPeer();
      coalescer.cancel();
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('online', onOnline);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [qc]);
}

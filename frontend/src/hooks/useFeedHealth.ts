import { useEffect, useState } from 'react';
import { streamLastActivityAt } from './useKiteLiveTicks';
import { deriveFeedHealth, useStreamHealthStore, type FeedHealth } from '../store/useStreamHealthStore';

/** The live-feed state to show the operator, re-evaluated every second. */
export function useFeedHealth(): FeedHealth {
  const transport = useStreamHealthStore((s) => s.transport);
  const transportSince = useStreamHealthStore((s) => s.transportSince);
  const interruptionSince = useStreamHealthStore((s) => s.interruptionSince);
  const broker = useStreamHealthStore((s) => s.broker);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (transport === 'idle') return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [transport]);

  return deriveFeedHealth({
    transport, transportSince, interruptionSince, broker, now, lastActivityAt: streamLastActivityAt(),
  });
}

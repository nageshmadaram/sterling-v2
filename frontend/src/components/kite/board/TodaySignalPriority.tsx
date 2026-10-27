import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { useView } from '../../../store/useViewStore';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { useEngineSignals } from '../../../hooks/useSterlingKiteEngine';
import { useSnapbackSnapshot } from '../../../hooks/useSnapback';
import { useGammaMoveSnapshot } from '../../../hooks/useGammaMove';
import type { EmaTrendSnapshot } from '../../../types/emaTrend';

export type TodaySignalStage = 'loading' | 'scanning' | 'ready' | 'error';
export type TodaySignalStrategy = 'SuperTrend' | 'Snapback' | 'Gamma-Unwind' | 'EMA Trend';

export interface TodaySignalPriorityState {
  day: string;
  olderReady: boolean;
  replay: boolean;
  stages: Record<TodaySignalStrategy, TodaySignalStage>;
}

const DEFAULT_PRIORITY: TodaySignalPriorityState = {
  day: '', olderReady: true, replay: false,
  stages: {
    SuperTrend: 'ready', Snapback: 'ready',
    'Gamma-Unwind': 'ready', 'EMA Trend': 'ready',
  },
};

const PriorityContext = React.createContext<TodaySignalPriorityState>(DEFAULT_PRIORITY);
export const useTodaySignalPriority = () => React.useContext(PriorityContext);

export const TODAY_STRATEGIES: readonly TodaySignalStrategy[] = [
  'SuperTrend', 'Snapback', 'Gamma-Unwind', 'EMA Trend',
];

export function istDay(date: Date): string {
  // Do not assume locale punctuation/order: browsers can format en-CA as
  // MM/DD/YYYY. The API and broker-evidence keys require YYYY-MM-DD.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date);
  const field = (name: string) => parts.find(part => part.type === name)?.value ?? '';
  return `${field('year')}-${field('month')}-${field('day')}`;
}

export function stageOf(
  query: { isError: boolean; isSuccess: boolean; dataUpdatedAt: number },
  scanned: boolean, currentDayStartMs: number,
): TodaySignalStage {
  if (query.isError) return 'error';
  if (!query.isSuccess || query.dataUpdatedAt < currentDayStartMs) return 'loading';
  return scanned ? 'scanning' : 'ready';
}

function activeJob(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false;
  const value = data as { scan?: { status?: string }; scanning?: boolean };
  return value.scanning === true ||
    ['QUEUED', 'RUNNING'].includes(value.scan?.status?.toUpperCase() || '');
}

/**
 * A single cross-strategy, current-IST-day read barrier. All four read-only
 * snapshots start concurrently even when their boards are hidden. Older
 * history/backfill is not requested until each current-day read has completed.
 * This does NOT change entry authorization or trigger broker orders.
 */
export function TodaySignalPriorityProvider({ children }: { children: React.ReactNode }) {
  const replay = useSimNowMs() !== null;
  const view = useView();
  const [day, setDay] = React.useState(() => istDay(new Date()));
  React.useEffect(() => {
    const id = window.setInterval(() => setDay(istDay(new Date())), 30_000);
    return () => window.clearInterval(id);
  }, []);

  // Reuse the same React Query keys as their boards: no second request is
  // needed merely because the user opens a different strategy tab.
  const supertrend = useEngineSignals(!replay);
  const snapback = useSnapbackSnapshot();
  const gamma = useGammaMoveSnapshot(true, 3_000);
  const ema = useQuery<EmaTrendSnapshot>({
    queryKey: ['ema-trend', 'snapshot', view, 'current', 'swing', 200, 'today', day],
    queryFn: () => api.get<EmaTrendSnapshot>(
      `/api/v1/ema-trend/snapshot?modes=swing&limit=200&day=${encodeURIComponent(day)}`),
    enabled: !replay,
    staleTime: 3_000,
    refetchInterval: 5_000,
  });

  const dayStart = Date.parse(`${day}T00:00:00+05:30`);
  const stages: TodaySignalPriorityState['stages'] = {
    SuperTrend: stageOf(supertrend, activeJob(supertrend.data), dayStart),
    Snapback: stageOf(snapback, activeJob(snapback.data), dayStart),
    'Gamma-Unwind': stageOf(gamma, activeJob(gamma.data), dayStart),
    'EMA Trend': stageOf(ema, activeJob(ema.data), dayStart),
  };

  // Once today's four snapshots have passed, a later intraday scan must not
  // hide history all over again. At the next IST date, gate anew.
  const [releasedDay, setReleasedDay] = React.useState('');
  const allLoaded = TODAY_STRATEGIES.every(name => stages[name] === 'ready');
  React.useEffect(() => {
    if (!replay && allLoaded) setReleasedDay(day);
  }, [allLoaded, day, replay]);
  const olderReady = replay || releasedDay === day;

  return <PriorityContext.Provider value={{ day, olderReady, replay, stages }}>
    {children}
  </PriorityContext.Provider>;
}

export function TodaySignalPriorityStatus() {
  const state = useTodaySignalPriority();
  if (state.replay) return null;
  const parts = TODAY_STRATEGIES.map(name => `${name}: ${
    state.stages[name] === 'ready' ? 'today loaded'
      : state.stages[name] === 'scanning' ? 'scanning today'
      : state.stages[name] === 'error' ? 'unavailable'
      : 'loading today'
  }`);
  return <div
    role="status" aria-live="polite" data-testid="today-signals-priority"
    style={{ padding: '5px 12px', fontSize: 11, lineHeight: 1.5,
      borderBottom: '1px solid var(--k-border)', color: 'var(--k-text)',
      background: 'var(--k-surface)', flexShrink: 0 }}
  >
    <strong>{state.olderReady
      ? 'Today’s four strategy boards loaded. History may now load.'
      : 'Priority: today’s signals across all four strategies. Older history is queued.'}</strong>
    <span style={{ marginLeft: 8, opacity: .8 }}>{parts.join(' · ')}</span>
    {!state.olderReady && TODAY_STRATEGIES.some(name => state.stages[name] === 'error') &&
      <span style={{ marginLeft: 8 }}>Unavailable live sources must recover before older history loads.</span>}
  </div>;
}

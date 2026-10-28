import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { useView } from '../../../store/useViewStore';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { useEngineSignals } from '../../../hooks/useSterlingKiteEngine';
import { useKiteStatus } from '../../../hooks/useKite';
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
  scanned: boolean, currentDayStartMs: number, scanFailed = false,
): TodaySignalStage {
  if (query.isError || scanFailed) return 'error';
  if (!query.isSuccess || query.dataUpdatedAt < currentDayStartMs) return 'loading';
  return scanned ? 'scanning' : 'ready';
}

function failedJob(data: unknown): boolean {
  if (!data || typeof data !== 'object') return false;
  const status = (data as { scan?: { status?: string } }).scan?.status?.toUpperCase() || '';
  return ['FAILED', 'ERROR', 'CANCELLED'].includes(status);
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
  // Live and simulation have separate caches, but one live view can contain
  // multiple Kite accounts. Releasing history for account A must never be
  // reused as proof the four current-day reads loaded for account B.
  const { data: kiteStatus } = useKiteStatus();
  const activeAccountId = kiteStatus?.account_id || 'no-active-account';
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
    SuperTrend: stageOf(supertrend, activeJob(supertrend.data), dayStart, failedJob(supertrend.data)),
    Snapback: stageOf(snapback, activeJob(snapback.data), dayStart, failedJob(snapback.data)),
    'Gamma-Unwind': stageOf(gamma, activeJob(gamma.data), dayStart, failedJob(gamma.data)),
    'EMA Trend': stageOf(ema, activeJob(ema.data), dayStart, failedJob(ema.data)),
  };

  // Once today's four snapshots have passed, a later intraday scan must not
  // hide history all over again. At the next IST date, gate anew.
  // The workspace may switch between live and simulation views without
  // reloading the page. A complete live-account read cannot release historical
  // queries for another tenant's view on the same calendar day.
  const scope = `${view}:${activeAccountId}:${day}`;
  const [releasedScope, setReleasedScope] = React.useState('');
  // An unavailable strategy must not freeze ALL other boards' history
  // indefinitely. Its current-day read was attempted and failed; preserve
  // the red error state while allowing older read-only rows once every
  // source has finished or failed. Never mistake a failed scan for a pass.
  const allLoaded = TODAY_STRATEGIES.every(name =>
    stages[name] === 'ready' || stages[name] === 'error');
  React.useEffect(() => {
    if (!replay && allLoaded) setReleasedScope(scope);
  }, [allLoaded, scope, replay]);
  const olderReady = replay || releasedScope === scope;

  return <PriorityContext.Provider value={{ day, olderReady, replay, stages }}>
    {children}
  </PriorityContext.Provider>;
}

export function TodaySignalPriorityStatus() {
  const state = useTodaySignalPriority();
  if (state.replay) return null;
  const failures = TODAY_STRATEGIES.filter(name => state.stages[name] === 'error');
  // A live scan has already returned a current-day API response, even if
  // the scan itself is still progressing. Count that response honestly while
  // keeping older history queued until the scan completes.
  const responsesReceived = TODAY_STRATEGIES.filter(name =>
    state.stages[name] === 'ready' || state.stages[name] === 'scanning').length;
  const parts = TODAY_STRATEGIES.map(name => `${name}: ${
    state.stages[name] === 'ready' ? 'response received'
      : state.stages[name] === 'scanning' ? 'scanning today'
      : state.stages[name] === 'error' ? 'unavailable'
      : 'loading today'
  }`);
  const summary = `Today · ${responsesReceived}/4 API responses${failures.length
    ? ` · ${failures.length} unavailable` : ''} · ${state.olderReady
      ? 'history ready' : 'older history queued'}`;
  return <div role="status" aria-live="polite"
    className="today-signals-priority" data-testid="today-signals-priority">
    <details className="today-priority-details">
      <summary aria-label="Expand current-day strategy loading details" title={summary}>
        <strong className="today-priority-summary-text">{summary}</strong>
        <span aria-hidden="true" className="today-priority-chevron">▾</span>
      </summary>
      <div className="today-priority-expanded">
        <p>IST {state.day} · {parts.join(' · ')}</p>
        <p>A response only confirms that the API responded, not that a fresh scan
          generated a signal. A failed strategy does not hide other boards' history.</p>
        {failures.length > 0 && <p role="alert">
          Unavailable: {failures.join(', ')}. Review the API and scan status.
        </p>}
      </div>
    </details>
  </div>;
}

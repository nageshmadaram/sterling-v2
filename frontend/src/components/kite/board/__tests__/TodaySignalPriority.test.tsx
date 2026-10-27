import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { istDay, stageOf, TodaySignalPriorityProvider,
  TodaySignalPriorityStatus, useTodaySignalPriority } from '../TodaySignalPriority';

const source = vi.hoisted(() => ({
  supertrend: { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } },
  snapback: { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } },
  gamma: { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scan: { status: 'COMPLETED' } } },
  replay: null as number | null,
}));
vi.mock('../../../../hooks/useSterlingKiteEngine', () => ({
  useEngineSignals: () => source.supertrend,
}));
vi.mock('../../../../hooks/useSnapback', () => ({
  useSnapbackSnapshot: () => source.snapback,
}));
vi.mock('../../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: () => source.gamma,
}));
vi.mock('../../../../hooks/useReplayStore', () => ({
  useSimNowMs: () => source.replay,
}));
vi.mock('../../../../store/useViewStore', () => ({
  useView: () => 'live',
}));
vi.mock('../../../../utils/api', () => ({
  api: { get: vi.fn(async () => ({ rows: [], scan: { status: 'COMPLETED' } })) },
}));

function Display() {
  const state = useTodaySignalPriority();
  return <div>
    <span data-testid="gate">{state.olderReady ? 'older-unlocked' : 'today-first'}</span>
    <TodaySignalPriorityStatus />
  </div>;
}
function wrap(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>
    <TodaySignalPriorityProvider>{children}</TodaySignalPriorityProvider>
  </QueryClientProvider>);
}

beforeEach(() => {
  source.supertrend = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } };
  source.snapback = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } };
  source.gamma = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scan: { status: 'COMPLETED' } } };
  source.replay = null;
});

describe('cross-strategy today priority', () => {
  it('uses YYYY-MM-DD and the IST trading date, regardless of test timezone', () => {
    expect(istDay(new Date('2026-10-26T18:40:00Z'))).toBe('2026-10-27');
    expect(istDay(new Date('2026-10-27T18:29:00Z'))).toBe('2026-10-27');
    expect(istDay(new Date('2026-10-27T18:31:00Z'))).toBe('2026-10-28');
  });

  it('distinguishes failed, running, stale and current-day completed sources', () => {
    const fresh = { isError: false, isSuccess: true, dataUpdatedAt: 20 };
    expect(stageOf(fresh, false, 10)).toBe('ready');
    expect(stageOf(fresh, true, 10)).toBe('scanning');
    expect(stageOf(fresh, false, 21)).toBe('loading');
    expect(stageOf({ ...fresh, isSuccess: false }, false, 10)).toBe('loading');
    expect(stageOf({ ...fresh, isError: true }, false, 10)).toBe('error');
  });

  it('releases history only after four live snapshots complete', async () => {
    wrap(<Display />);
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('older-unlocked'));
    expect(screen.getByRole('status')).toHaveTextContent('Today’s four strategy boards loaded');
    expect(screen.getByRole('status')).toHaveTextContent('EMA Trend: today loaded');
  });

  it('keeps older history deferred when one current-day scan is running', async () => {
    source.snapback = { ...source.snapback, data: { scanning: true } };
    wrap(<Display />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Snapback: scanning today'));
    expect(screen.getByTestId('gate')).toHaveTextContent('today-first');
    expect(screen.getByRole('status')).toHaveTextContent('Older history is queued');
  });

  it('keeps older history deferred and explains a failed primary feed', async () => {
    source.gamma = { ...source.gamma, isError: true };
    wrap(<Display />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Gamma-Unwind: unavailable'));
    expect(screen.getByTestId('gate')).toHaveTextContent('today-first');
    expect(screen.getByRole('status')).toHaveTextContent('must recover before older history loads');
  });
});

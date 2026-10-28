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
  accountId: 'kite-account-a',
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
vi.mock('../../../../hooks/useKite', () => ({
  useKiteStatus: () => ({ data: { account_id: source.accountId } }),
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
  const shell = (content: React.ReactNode) => <QueryClientProvider client={client}>
    <TodaySignalPriorityProvider>{content}</TodaySignalPriorityProvider>
  </QueryClientProvider>;
  const result = render(shell(children));
  return { ...result, rerenderWith: (content: React.ReactNode) => result.rerender(shell(content)) };
}

beforeEach(() => {
  source.supertrend = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } };
  source.snapback = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scanning: false } };
  source.gamma = { isError: false, isSuccess: true, dataUpdatedAt: Date.now(), data: { scan: { status: 'COMPLETED' } } };
  source.accountId = 'kite-account-a';
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
    expect(stageOf(fresh, false, 10, true)).toBe('error');
  });

  it('releases history only after four live snapshots complete', async () => {
    wrap(<Display />);
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('older-unlocked'));
    expect(screen.getByRole('status')).toHaveTextContent('Today · 4/4 API responses · history ready');
    expect(screen.getByRole('status')).toHaveTextContent('EMA Trend: response received');
    expect(screen.getByRole('status')).toHaveTextContent('▾');
    expect(screen.getByRole('status')).toHaveTextContent('A response only confirms that the API responded');
    expect(screen.getByRole('status')).toHaveTextContent(/IST \d{4}-\d{2}-\d{2}/);
  });

  it('keeps the current-day API explanation collapsed until explicitly expanded', async () => {
    wrap(<Display />);
    const summary = screen.getByLabelText('Expand current-day strategy loading details');
    expect(summary.tagName).toBe('SUMMARY');
    expect(summary.closest('details')).not.toHaveAttribute('open');
    // A single compact summary remains visible, with full facts available.
    expect(summary).toHaveTextContent('Today ·');
    summary.click();
    expect(summary.closest('details')).toHaveAttribute('open');
  });

  it('keeps older history deferred when one current-day scan is running', async () => {
    source.snapback = { ...source.snapback, data: { scanning: true } };
    wrap(<Display />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Snapback: scanning today'));
    // Scan-in-progress has already returned an API response; do not falsely
    // report only 3/4 responses while still correctly queuing older history.
    expect(screen.getByRole('status')).toHaveTextContent('Today · 4/4 API responses');
    expect(screen.getByTestId('gate')).toHaveTextContent('today-first');
    expect(screen.getByRole('status')).toHaveTextContent('older history queued');
  });

  it('restarts the today-first barrier when a different Kite account is selected', async () => {
    const screenView = wrap(<Display />);
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('older-unlocked'));

    // The account mutation resets cached strategy queries while the next
    // account's current-day snapshots are still pending.
    source.accountId = 'kite-account-b';
    source.snapback = { ...source.snapback, isSuccess: false, dataUpdatedAt: 0 };
    screenView.rerenderWith(<Display />);
    expect(screen.getByTestId('gate')).toHaveTextContent('today-first');
    expect(screen.getByRole('status')).toHaveTextContent('older history queued');

    source.snapback = { ...source.snapback, isSuccess: true, dataUpdatedAt: Date.now() };
    screenView.rerenderWith(<Display />);
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('older-unlocked'));
  });

  it("releases older read-only history after all today's checks settle, while showing the failed strategy", async () => {
    source.gamma = { ...source.gamma, isError: true };
    wrap(<Display />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Gamma-Unwind: unavailable'));
    await waitFor(() => expect(screen.getByTestId('gate')).toHaveTextContent('older-unlocked'));
    expect(screen.getByRole('status')).toHaveTextContent('1 unavailable');
    expect(screen.getByRole('status')).toHaveTextContent('Gamma-Unwind: unavailable');
  });
});

import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { useReplayTransport } from '../useReplayTransport';
import { useReplayStore } from '../useReplayStore';
import { authenticatedFetch } from '../../utils/auth';
import { syncReplayStatus } from '../useReplayStream';

vi.mock('../../utils/auth', () => ({ authenticatedFetch: vi.fn() }));
vi.mock('../useReplayStream', () => ({ syncReplayStatus: vi.fn() }));
vi.mock('../../components/kite/replay/replayToastBus', () => ({ pushReplayToast: vi.fn() }));

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}
beforeEach(() => {
  vi.clearAllMocks();
  useReplayStore.getState().reset();
});
afterEach(() => vi.useRealTimers());
function refusal(code: string, message: string, status = 409) {
  return new Response(JSON.stringify({ detail: { code, message } }), { status });
}

describe('replay ownership refusals', () => {
  it.each([
    ['replay_busy', 'The replay slot is in use.', 409],
    ['replay_owned_elsewhere', 'This replay belongs to another operator.', 409],
    ['invalid_date_range', 'Start date must precede end date.', 400],
  ])('shows %s without attempting Stop', async (code, message, status) => {
    vi.mocked(authenticatedFetch).mockResolvedValueOnce(refusal(code, message, status));
    const hook = renderHook(() => useReplayTransport(), { wrapper });
    await act(async () => { await hook.result.current.start(); });
    expect(authenticatedFetch).toHaveBeenCalledTimes(1);
    expect(authenticatedFetch).toHaveBeenCalledWith('/api/v1/simulation/start', expect.any(Object));
    expect(useReplayStore.getState().error).toMatchObject({ code, message });
  });

  it('restarts only a session explicitly reported as already running', async () => {
    vi.useFakeTimers();
    const idle = useReplayStore.getState().status;
    const running = { ...idle, state: 'running' as const };
    vi.mocked(authenticatedFetch)
      .mockResolvedValueOnce(refusal('already_running', 'Your replay is already running.'))
      .mockResolvedValueOnce(new Response(JSON.stringify(idle)))
      .mockResolvedValueOnce(new Response(JSON.stringify(running)));
    vi.mocked(syncReplayStatus).mockResolvedValue(running);
    const hook = renderHook(() => useReplayTransport(), { wrapper });
    await act(async () => {
      const started = hook.result.current.start();
      await vi.advanceTimersByTimeAsync(710);
      await started;
    });
    expect(vi.mocked(authenticatedFetch).mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/simulation/start', '/api/v1/simulation/stop', '/api/v1/simulation/start',
    ]);
    expect(useReplayStore.getState().status.state).toBe('running');
  });
});

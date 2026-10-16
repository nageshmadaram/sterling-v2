import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../utils/api';
import { useEmaTrendExecution, useUpdateEmaTrend } from '../useEmaTrend';
vi.mock('../../utils/api', () => ({ api: { post: vi.fn(), patch: vi.fn() } }));
let replay: number | null = null;
vi.mock('../useReplayStore', () => ({ useSimNowMs: () => replay, useReplayStore: () => 'session-1' }));
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}
const plan = { id: 'p-7', revision: 1, status: 'READY', blockers: [] };
beforeEach(() => { vi.clearAllMocks(); replay = null; });
describe('EMA Trend strategy-owned transport', () => {
  it('sends only changed configuration with the revision being edited', async () => {
    vi.mocked(api.patch).mockResolvedValue({ revision: 8 });
    const hook = renderHook(useUpdateEmaTrend, { wrapper });
    await act(async () => { await hook.result.current.mutateAsync({ expected_revision: 7, changes: { enabled: true } }); });
    expect(api.patch).toHaveBeenCalledWith('/api/v1/ema-trend/config', { expected_revision: 7, changes: { enabled: true } });
  });
  it('arms only the server-published revision and never supplies browser quantity or stop', async () => {
    vi.mocked(api.post).mockResolvedValueOnce(plan).mockResolvedValueOnce({ status: 'ACKNOWLEDGED', campaign: { id: 'campaign-1' } });
    const hook = renderHook(useEmaTrendExecution, { wrapper });
    await act(async () => { await hook.result.current.mutateAsync({ signalId: 'signal-1', configRevision: 7 }); });
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/v1/ema-trend/plans', { signal_id: 'signal-1', expected_config_revision: 7 });
    expect(api.post).toHaveBeenNthCalledWith(2, '/api/v1/ema-trend/plans/p-7/arm', { expected_revision: 1, idempotency_key: 'ema-trend-ui:p-7:1', execution_mode: 'PAPER' });
  });
  it('does not arm a blocked or malformed plan', async () => {
    vi.mocked(api.post).mockResolvedValue({ ...plan, status: 'BLOCKED', blockers: ['NO_RISK_CAPACITY'] });
    const hook = renderHook(useEmaTrendExecution, { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync({ signalId: 's', configRevision: 7 })).rejects.toThrow('NO_RISK_CAPACITY'); });
    expect(api.post).toHaveBeenCalledTimes(1);
  });
  it.each(['UNKNOWN', 'REJECTED', 'HALTED'])('does not label %s as an execution success or retry it', async status => {
    vi.mocked(api.post).mockResolvedValueOnce(plan).mockResolvedValueOnce({ status, campaign: { id: 'campaign-1' } });
    const hook = renderHook(useEmaTrendExecution, { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync({ signalId: 's', configRevision: 7 })).rejects.toThrow(status); });
    expect(api.post).toHaveBeenCalledTimes(2);
  });
  it('rejects historical replay before any mutation', async () => {
    replay = 123;
    const hook = renderHook(useEmaTrendExecution, { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync({ signalId: 's', configRevision: 7 })).rejects.toThrow('read-only'); });
    expect(api.post).not.toHaveBeenCalled();
  });
});

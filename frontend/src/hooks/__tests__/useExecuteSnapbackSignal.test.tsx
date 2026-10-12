import React from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useExecuteSnapbackSignal } from '../useSnapback';
import { api } from '../../utils/api';
import { notifyOrder } from '../../store/useKiteNotifications';
vi.mock('../../utils/api', () => ({ api: { post: vi.fn() } }));
vi.mock('../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{children}</QueryClientProvider>;
}
beforeEach(() => vi.clearAllMocks());
const published = { ok: true, plan_id: 'owned-plan', revision: 4, status: 'PUBLISHED' };
const armed = { plan_id: 'owned-plan', revision: 5, status: 'ARMED', mode: 'LIVE', idempotent_replay: false, execution: 'scheduled' };
describe('Snapback owned publish/arm transport', () => {
  it('arms only the published revision with a stable idempotency identity', async () => {
    vi.mocked(api.post).mockResolvedValueOnce(published).mockResolvedValueOnce(armed);
    const hook = renderHook(() => useExecuteSnapbackSignal(), { wrapper });
    await act(async () => { await hook.result.current.mutateAsync('signal-1'); });
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/v1/snapback/plans', { signal_id: 'signal-1' });
    expect(api.post).toHaveBeenNthCalledWith(2, '/api/v1/snapback/plans/owned-plan/arm', {
      expected_revision: 4, idempotency_key: 'snapback-ui:owned-plan:4', mode: 'LIVE',
    });
    expect(notifyOrder).toHaveBeenCalledWith(expect.objectContaining({ title: 'Snapback armed' }));
  });
  it('surfaces a stale-plan refusal without substituting or retrying its revision', async () => {
    vi.mocked(api.post).mockResolvedValueOnce(published).mockRejectedValueOnce(new Error('Plan revision is stale; republish'));
    const hook = renderHook(() => useExecuteSnapbackSignal(), { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync('signal-1')).rejects.toThrow('stale'); });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(notifyOrder).toHaveBeenCalledWith(expect.objectContaining({ title: 'Snapback blocked', message: 'Plan revision is stale; republish' }));
  });
  it.each([
    { ...armed, status: 'REJECTED' }, { ...armed, plan_id: 'other-plan' },
    { ...armed, revision: 4 }, { ...armed, mode: 'PAPER' },
  ])('never reports an unconfirmed arm as success (%j)', async response => {
    vi.mocked(api.post).mockResolvedValueOnce(published).mockResolvedValueOnce(response);
    const hook = renderHook(() => useExecuteSnapbackSignal(), { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync('signal-1')).rejects.toThrow('not confirmed'); });
    expect(notifyOrder).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'Snapback armed' }));
  });
  it('refuses malformed published revisions before arming', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ ...published, revision: 1.5 });
    const hook = renderHook(() => useExecuteSnapbackSignal(), { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync('signal-1')).rejects.toThrow('not published'); });
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

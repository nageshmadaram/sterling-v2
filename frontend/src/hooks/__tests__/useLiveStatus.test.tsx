/**
 * Resuming from safe mode must speak the backend's contract: the confirmation phrase
 * it names, a reason, and the investigation acknowledgement. A 404 on live-status must
 * read as "not available", never as "healthy".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const get = vi.fn();
const post = vi.fn();
vi.mock('../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } }));
vi.mock('../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

import { useLiveStatus, useResumeSafeMode } from '../useLiveStatus';

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => { get.mockReset(); post.mockReset(); });

describe('useResumeSafeMode', () => {
  it('sends the phrase the backend names, the reason and the acknowledgement', async () => {
    get.mockResolvedValue({ confirmation_phrase: 'RESUME TRADING' });
    post.mockResolvedValue({ resumed: true });
    const { result } = renderHook(() => useResumeSafeMode(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ reason: 'Stops re-placed', acknowledgeInvestigation: true });
    });
    expect(post).toHaveBeenCalledWith('/api/v1/ops/safe-mode/resume', {
      confirm: 'RESUME TRADING', reason: 'Stops re-placed', acknowledge_investigation: true,
    });
  });

  it('falls back to the documented phrase when the detail read fails', async () => {
    get.mockRejectedValue(new Error('boom'));
    post.mockResolvedValue({});
    const { result } = renderHook(() => useResumeSafeMode(), { wrapper });
    await act(async () => { await result.current.mutateAsync({ reason: 'Operator check' }); });
    expect(post.mock.calls[0][1]).toEqual({
      confirm: 'RESUME TRADING', reason: 'Operator check', acknowledge_investigation: false,
    });
  });
});

describe('useLiveStatus', () => {
  it('reads an unavailable endpoint (404) as not available', async () => {
    get.mockRejectedValue(Object.assign(new Error('nf'), { status: 404 }));
    const { result } = renderHook(() => useLiveStatus(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual({ available: false, status: null });
  });
});

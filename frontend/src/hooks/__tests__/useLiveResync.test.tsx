import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MIN_AWAY_MS, useLiveResync } from '../useLiveResync';
import { __emitPeerMessage, __resetLiveSync, requestResync } from '../../utils/liveSync';

let qc: QueryClient;
let invalidate: ReturnType<typeof vi.fn>;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={qc}>{children}</QueryClientProvider>
);

function setVisibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-05T09:30:00Z'));
  qc = new QueryClient();
  invalidate = vi.fn().mockResolvedValue(undefined);
  qc.invalidateQueries = invalidate as unknown as QueryClient['invalidateQueries'];
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
});
afterEach(() => {
  __resetLiveSync();
  vi.useRealTimers();
});

describe('useLiveResync', () => {
  it('refetches trading state when a reconnect asks for it', async () => {
    renderHook(() => useLiveResync(), { wrapper });
    requestResync('ws-reconnect');
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
    const arg = invalidate.mock.calls[0][0] as unknown as { predicate: (q: { queryKey: unknown[] }) => boolean };
    expect(arg.predicate({ queryKey: ['kite-positions'] })).toBe(true);
    expect(arg.predicate({ queryKey: ['kite-historical', 1] })).toBe(false);
  });

  it('refetches when the browser comes back online', async () => {
    renderHook(() => useLiveResync(), { wrapper });
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('refetches when the tab returns after being away, not after a quick alt-tab', async () => {
    renderHook(() => useLiveResync(), { wrapper });

    setVisibility('hidden');
    await vi.advanceTimersByTimeAsync(1_000);
    setVisibility('visible');
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).not.toHaveBeenCalled();

    setVisibility('hidden');
    await vi.advanceTimersByTimeAsync(MIN_AWAY_MS + 1_000);
    setVisibility('visible');
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('refetches when another tab says it wrote something', async () => {
    renderHook(() => useLiveResync(), { wrapper });
    __emitPeerMessage({ t: 'resync', from: 'other-tab' });
    await vi.advanceTimersByTimeAsync(300);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('collapses a burst of triggers into one refetch', async () => {
    renderHook(() => useLiveResync(), { wrapper });
    requestResync('ws-reconnect');
    requestResync('boot-change');
    requestResync('broker-reconnect');
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(400);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('stops listening on unmount', async () => {
    const { unmount } = renderHook(() => useLiveResync(), { wrapper });
    unmount();
    requestResync('ws-reconnect');
    window.dispatchEvent(new Event('online'));
    await vi.advanceTimersByTimeAsync(2_000);
    expect(invalidate).not.toHaveBeenCalled();
  });
});

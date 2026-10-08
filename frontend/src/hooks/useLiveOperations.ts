/**
 * The Operations screen's data source.
 *
 * `retry: false` and `staleTime: 0` are deliberate. This is a safety screen: an
 * unreachable backend must be visible immediately, not smoothed over by retries
 * while the page keeps showing the last green state. The caller is expected to
 * treat `isError` as authoritative and ignore cached data for the status
 * verdict, however recent that data looks.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import type { LiveOperations } from '../types/liveOperations';

export const LIVE_OPERATIONS_PATH = '/api/v1/operations/family';
// The key string is shared with the live-sync refetch list; do not rename it here alone.
export const LIVE_OPERATIONS_KEY = ['family-operations-v2'];

/** Ten seconds: fast enough that a stopped system is noticed, slow enough to be free. */
export const LIVE_OPERATIONS_POLL_MS = 10_000;

export function useLiveOperations() {
  return useQuery<LiveOperations>({
    queryKey: LIVE_OPERATIONS_KEY,
    queryFn: () => api.get<LiveOperations>(LIVE_OPERATIONS_PATH),
    refetchInterval: LIVE_OPERATIONS_POLL_MS,
    staleTime: 0,
    // No retry: a safety screen that hides an outage behind three attempts is
    // showing a system state that may no longer exist.
    retry: false,
    refetchOnWindowFocus: true,
  });
}

/** Seconds since the payload was generated, or null when it cannot be computed. */
export function dataAgeSeconds(generatedAt: string | undefined, now: number = Date.now()): number | null {
  if (!generatedAt) return null;
  const parsed = Date.parse(generatedAt);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.round((now - parsed) / 1000));
}

/** Stop and resume new trades. Stop is one click; resume is the one that can lose money. */
export function useOperatorTradeHalt() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: LIVE_OPERATIONS_KEY });

  const stop = useMutation({
    mutationFn: (reason: string) =>
      api.post(`/api/v1/snapback/family/stop-new-trades?reason=${encodeURIComponent(reason)}`),
    onSuccess: invalidate,
  });

  const resume = useMutation({
    mutationFn: (reason: string) =>
      api.post(`/api/v1/snapback/family/resume-new-trades?reason=${encodeURIComponent(reason)}`),
    onSuccess: invalidate,
  });

  return { stop, resume };
}

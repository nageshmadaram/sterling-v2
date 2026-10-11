// Kite-specific Telegram targets — TanStack Query hooks.
// Uses the same api client / base-url pattern as useKite.ts (api.get/post/put/delete
// against `/api/v1/kite/...`). Every mutation invalidates the targets list so the
// UI reflects the server state without a manual refetch.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';
import type {
  KiteTelegramTarget, KiteTelegramTargetIn, KiteTelegramTargetList, KiteTelegramTargetPatch,
  KiteTelegramStatus, KiteTelegramControlCheck,
} from '../types/kiteTelegram';

const K = '/api/v1/kite/telegram';
const KEY = ['kite-telegram-targets'];

export function useKiteTelegramStatus() {
  return useQuery<KiteTelegramStatus>({
    queryKey: [...KEY, 'status'], queryFn: () => api.get<KiteTelegramStatus>(`${K}/status`),
    refetchInterval: 15_000,
  });
}

export function useCheckKiteTelegramControl() {
  return useMutation<KiteTelegramControlCheck, Error, void>({
    mutationFn: () => api.post<KiteTelegramControlCheck>(`${K}/control/check`),
  });
}

export function useSetKiteTelegramSignalAlerts() {
  const qc = useQueryClient();
  return useMutation<{ signal_alerts_enabled: boolean }, Error, boolean>({
    mutationFn: (enabled) => api.put(`${K}/preferences`, { signal_alerts_enabled: enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: [...KEY, 'status'] }); },
  });
}

export function useKiteTelegramTargets() {
  return useQuery<KiteTelegramTargetList>({
    queryKey: KEY,
    queryFn: () => api.get<KiteTelegramTargetList>(K),
    staleTime: 30_000,
  });
}

export function useAddKiteTelegram() {
  const qc = useQueryClient();
  return useMutation<KiteTelegramTarget, Error, KiteTelegramTargetIn>({
    mutationFn: (body) => api.post<KiteTelegramTarget>(K, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: KEY });
      notifyOrder({ kind: 'info', title: 'Telegram bot added', message: `Bot "${data.label}" registered.` });
    },
    onError: (err) => {
      notifyOrder({ kind: 'error', title: 'Could not add bot', message: err.message || 'Failed to register Telegram bot.' });
    },
  });
}

export function useUpdateKiteTelegram() {
  const qc = useQueryClient();
  return useMutation<KiteTelegramTarget, Error, { id: string } & KiteTelegramTargetPatch>({
    mutationFn: ({ id, ...body }) => api.put<KiteTelegramTarget>(`${K}/${id}`, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: KEY });
      notifyOrder({ kind: 'info', title: 'Telegram bot updated', message: `Bot "${data.label}" updated.` });
    },
    onError: (err) => {
      notifyOrder({ kind: 'error', title: 'Update failed', message: err.message || 'Failed to update Telegram bot.' });
    },
  });
}

export function useDeleteKiteTelegram() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => api.delete<{ ok: boolean }>(`${K}/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      notifyOrder({ kind: 'info', title: 'Telegram bot removed', message: 'Bot removed.' });
    },
    onError: (err) => {
      notifyOrder({ kind: 'error', title: 'Delete failed', message: err.message || 'Could not delete Telegram bot.' });
    },
  });
}

export function useTestKiteTelegram() {
  const qc = useQueryClient();
  return useMutation<KiteTelegramTarget, Error, string>({
    mutationFn: (id) => api.post<KiteTelegramTarget>(`${K}/${id}/test`),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: KEY });
      if (data.reachable) {
        notifyOrder({ kind: 'info', title: 'Telegram test passed', message: `Test message delivered to ${data.label}.` });
      } else {
        notifyOrder({ kind: 'error', title: 'Telegram test failed', message: 'Could not deliver test message — check token and chat ID.' });
      }
    },
    onError: (err) => {
      notifyOrder({ kind: 'error', title: 'Telegram test error', message: err.message || 'Test request failed.' });
    },
  });
}

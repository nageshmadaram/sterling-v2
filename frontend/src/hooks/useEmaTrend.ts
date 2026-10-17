import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useSimNowMs, useReplayStore } from './useReplayStore';
import { useView } from '../store/useViewStore';
import type { EmaTrendCampaign, EmaTrendConfig, EmaTrendConfigResponse, EmaTrendFeature, EmaTrendMode, EmaTrendPlan, EmaTrendReplayRun, EmaTrendScan, EmaTrendSnapshot, EmaTrendLiveReadiness } from '../types/emaTrend';

export const EMA_TREND_BASE = '/api/v1/ema-trend';
export const EMA_TREND_KEY = ['ema-trend'] as const;
function useScope() {
  const view = useView();
  const replay = useSimNowMs() !== null;
  const session = useReplayStore(s => s.status.session_id);
  return { replay, key: [view, replay ? session || 'replay' : 'current'] };
}
export function useEmaTrendConfig() {
  const scope = useScope();
  return useQuery<EmaTrendConfigResponse>({ queryKey: [...EMA_TREND_KEY, 'config', ...scope.key],
    queryFn: () => api.get(`${EMA_TREND_BASE}/config`), staleTime: 15_000 });
}
/** Poll each selected mode separately so a high-volume mode never hides
 * rows from another mode. The menu changes the view, not scan configuration. */
export function useEmaTrendSnapshot(modes?: readonly EmaTrendMode[]) {
  const scope = useScope();
  const key = modes?.join(',') || 'all';
  // Historical replay has its own immutable server view. Never substitute the live snapshot.
  return useQuery<EmaTrendSnapshot>({ queryKey: [...EMA_TREND_KEY, 'snapshot', ...scope.key, key],
    queryFn: async () => {
      if (!modes?.length) return api.get<EmaTrendSnapshot>(`${EMA_TREND_BASE}/snapshot`);
      const snapshots = await Promise.all(modes.map(mode =>
        api.get<EmaTrendSnapshot>(`${EMA_TREND_BASE}/snapshot?mode=${encodeURIComponent(mode)}`)));
      const first = snapshots[0];
      return {
        ...first,
        rows: snapshots.flatMap(snapshot => snapshot.rows),
        campaigns: snapshots.flatMap(snapshot => snapshot.campaigns),
        generated_ms: Math.max(...snapshots.map(snapshot => snapshot.generated_ms || 0)),
        truncatedModes: modes.filter((_, index) => snapshots[index].next_cursor != null),
      };
    },
    enabled: !scope.replay, refetchInterval: 10_000, staleTime: 3_000 });
}
export function useEmaTrendLiveReadiness() {
  const scope = useScope();
  return useQuery<EmaTrendLiveReadiness>({
    queryKey: [...EMA_TREND_KEY, 'live-readiness', ...scope.key],
    queryFn: () => api.get(`${EMA_TREND_BASE}/live-readiness`),
    // A fresh account validation touches Kite. Do it only on explicit request,
    // not in React polling or on every settings page render.
    enabled: false, retry: false,
  });
}
export function useUpdateEmaTrend() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: { expected_revision: number; changes: Partial<EmaTrendConfig> }) =>
    api.patch<EmaTrendConfigResponse>(`${EMA_TREND_BASE}/config`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
export function useRunEmaTrendScan() {
  const qc = useQueryClient();
  const config = useEmaTrendConfig();
  return useMutation({ mutationFn: (modes?: EmaTrendMode[]) => {
    if (!config.data || config.isError) throw new Error('Read the current EMA Trend configuration before scanning.');
    return api.post<EmaTrendScan>(`${EMA_TREND_BASE}/scan`, { modes: modes || config.data.config.modes, expected_revision: config.data.revision });
  }, onSuccess: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
/** A scan request queues durable work; shared scan sweeps must await that job, not only its ACK. */
export async function waitForEmaTrendScan(initial: EmaTrendScan, cancelled: () => boolean = () => false): Promise<EmaTrendScan> {
  if (!initial?.id) throw new Error('The server did not return an EMA Trend scan identity.');
  let job = initial;
  for (let pass = 0; pass < 600 && ['QUEUED', 'RUNNING'].includes(job.status); pass += 1) {
    if (cancelled()) await api.post(`${EMA_TREND_BASE}/scans/${encodeURIComponent(job.id)}/cancel`, {});
    await new Promise(resolve => setTimeout(resolve, 1_000));
    job = await api.get<EmaTrendScan>(`${EMA_TREND_BASE}/scans/${encodeURIComponent(job.id)}`);
  }
  if (job.status !== 'COMPLETED') throw new Error(`EMA Trend scan ${job.status.toLowerCase()}: ${(job.errors || []).join(' · ')}`);
  return job;
}
export function useEmaTrendScan(jobId?: string) {
  return useQuery<EmaTrendScan>({ queryKey: [...EMA_TREND_KEY, 'scan', jobId],
    queryFn: () => api.get(`${EMA_TREND_BASE}/scans/${encodeURIComponent(jobId!)}`), enabled: Boolean(jobId),
    refetchInterval: query => ['RUNNING', 'QUEUED'].includes(query.state.data?.status || '') ? 1_000 : false });
}
export function useCancelEmaTrendScan() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.post(`${EMA_TREND_BASE}/scans/${encodeURIComponent(id)}/cancel`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
export function useEmaTrendExecution() {
  const qc = useQueryClient();
  const replay = useSimNowMs() !== null;
  return useMutation({ mutationFn: async (input: { signalId: string; configRevision: number }) => {
    if (replay) throw new Error('Historical replay is read-only.');
    const plan = await api.post<EmaTrendPlan>(`${EMA_TREND_BASE}/plans`, {
      signal_id: input.signalId, expected_config_revision: input.configRevision,
    });
    if (plan.status !== 'READY' || !plan.id || !Number.isInteger(plan.revision)) {
      throw new Error(plan.blockers?.join(' · ') || 'The server did not publish a ready paper plan.');
    }
    const armed = await api.post<{ status: string; campaign?: { id: string }; execution?: { error?: string } }>(`${EMA_TREND_BASE}/plans/${encodeURIComponent(plan.id)}/arm`, {
      expected_revision: plan.revision, idempotency_key: `ema-trend-ui:${plan.id}:${plan.revision}`, execution_mode: 'PAPER',
    });
    if (!armed.campaign?.id || !['ALREADY_ARMED', 'ACKNOWLEDGED', 'PARTIAL', 'FILLED'].includes(armed.status)) {
      throw new Error(`Paper request ${armed.status || 'unconfirmed'}. ${armed.execution?.error || 'Refresh campaign state before retrying.'}`);
    }
    return armed;
  }, onSettled: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
export function useEmaTrendCampaignAction() {
  const qc = useQueryClient();
  const replay = useSimNowMs() !== null;
  return useMutation({ mutationFn: (input: { campaignId: string; revision: number; action: 'close' | 'reduce'; percent?: 25 | 50 | 100 }) => {
    if (replay) throw new Error('Historical replay is read-only.');
    return api.post<{ status: string; execution?: { error?: string } }>(`${EMA_TREND_BASE}/campaigns/${encodeURIComponent(input.campaignId)}/actions`, {
      action: input.action, percent: input.percent, expected_revision: input.revision,
      idempotency_key: `ema-trend-ui:${input.campaignId}:${input.revision}:${input.action}:${input.percent || 100}`,
    }).then(result => {
      if (!['CLOSED', 'ACKNOWLEDGED', 'PARTIAL', 'FILLED', 'UNRESOLVED'].includes(result.status)) throw new Error(`Paper action ${result.status || 'unconfirmed'}. ${result.execution?.error || 'Reconcile campaign state before retrying.'}`);
      return result;
    });
  }, onSettled: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
export function useEmaTrendOperation() {
  const qc = useQueryClient();
  const replay = useSimNowMs() !== null;
  return useMutation({ mutationFn: (operation: 'pause' | 'resume' | 'flatten') => {
    if (replay) throw new Error('Historical replay is read-only.');
    return api.post(`${EMA_TREND_BASE}/operations/${operation}`, {});
  }, onSuccess: () => qc.invalidateQueries({ queryKey: EMA_TREND_KEY }) });
}
export function useEmaTrendChart(symbol: string, mode: EmaTrendMode, signalId?: string, configRevision?: number) {
  const scope = useScope();
  const params = new URLSearchParams({ symbol, mode });
  if (signalId) params.set('signal_id', signalId);
  if (configRevision != null) params.set('expected_config_revision', String(configRevision));
  return useQuery<{ symbol: string; mode: EmaTrendMode; features: EmaTrendFeature[]; blockers?: string[]; source_close_ms?: number }>({
    queryKey: [...EMA_TREND_KEY, 'chart', ...scope.key, symbol, mode, signalId, configRevision],
    queryFn: () => api.get(`${EMA_TREND_BASE}/chart?${params}`),
    enabled: !scope.replay && Boolean(symbol), staleTime: 30_000,
  });
}
export function useEmaTrendReplay() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: { symbol: string; mode: EmaTrendMode; expected_config_revision: number }) =>
    api.post<EmaTrendReplayRun>(`${EMA_TREND_BASE}/replay`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...EMA_TREND_KEY, 'runs'] }) });
}
export function useEmaTrendRuns() {
  const scope = useScope();
  return useQuery<{ items: EmaTrendReplayRun[] }>({ queryKey: [...EMA_TREND_KEY, 'runs', ...scope.key],
    queryFn: () => api.get(`${EMA_TREND_BASE}/runs`), staleTime: 5_000 });
}
export function useEmaTrendRun(id?: string) {
  const scope = useScope();
  return useQuery<EmaTrendReplayRun>({ queryKey: [...EMA_TREND_KEY, 'run', ...scope.key, id],
    queryFn: () => api.get(`${EMA_TREND_BASE}/runs/${encodeURIComponent(id!)}`), enabled: Boolean(id),
    refetchInterval: query => ['QUEUED', 'RUNNING'].includes(query.state.data?.status || '') ? 1_000 : false });
}
export function useEmaTrendCampaign(id?: string, enabled = true) {
  const scope = useScope();
  return useQuery<{ campaign: EmaTrendCampaign; events: Array<{ id?: string; kind?: string; type?: string; at_ms?: number }> }>({
    queryKey: [...EMA_TREND_KEY, 'campaign', ...scope.key, id],
    queryFn: () => api.get(`${EMA_TREND_BASE}/campaigns/${encodeURIComponent(id!)}`),
    enabled: enabled && !scope.replay && Boolean(id), refetchInterval: 5_000, staleTime: 0,
  });
}

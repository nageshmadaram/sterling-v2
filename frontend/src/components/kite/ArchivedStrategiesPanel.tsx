import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useIsSimulation } from '../../store/useViewStore';

type Strategy = { id: string; name: string; description: string; enabled: boolean };
type Response = { strategies: Strategy[] };
const KEY = ['archived-strategies'];
const BASE = '/api/v1/config/archived-strategies';

export function ArchivedStrategiesPanel() {
  const sim = useIsSimulation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: KEY, enabled: !sim,
    queryFn: async () => await api.get<Response>(BASE) });
  const mutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) =>
      await api.put<Response>(`${BASE}/${id}`, { enabled }),
    onSuccess: (data) => {
      client.setQueryData(KEY, data);
      void client.invalidateQueries({ queryKey: ['adaptive-edge-config'] });
      void client.invalidateQueries({ queryKey: ['intraday-config'] });
      void client.invalidateQueries({ queryKey: ['navigator-config'] });
    },
  });
  const message = (error: unknown) => {
    const e = error as { response?: { data?: { detail?: string } }; message?: string };
    return e.response?.data?.detail || e.message || 'Unable to save strategy settings.';
  };
  if (sim) return <p>Archived strategy controls are available in Live settings.</p>;
  return <section aria-label="Archived strategies">
    <p>Restore an archived scanner when needed. Disabling stops new scans and entries; existing positions keep reconciliation, protection and exits.</p>
    <p>Enabling does not bypass validation, promotion, SAFE_MODE or broker safety checks. Adaptive Edge and Intraday ALGO are reset to off when restored. Opening Leaders still requires shared ALGO and approved evidence. Navigator is advisory.</p>
    {query.isLoading && <p role="status">Loading archived strategies…</p>}
    {query.isError && <div role="alert">{message(query.error)} <button type="button" className="sterling-retry-button" onClick={() => void query.refetch()}>Retry</button></div>}
    {mutation.isError && <p role="alert">{message(mutation.error)}</p>}
    {query.data?.strategies.map((strategy) => <div key={strategy.id} style={{ padding: 16, marginBottom: 12, border: '1px solid var(--k-border)', borderRadius: 8 }}>
      <label style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between' }}>
        <strong>{strategy.name}</strong>
        <span><input type="checkbox" aria-label={`Enable ${strategy.name}`} checked={strategy.enabled}
          disabled={mutation.isPending} onChange={() => mutation.mutate({ id: strategy.id, enabled: !strategy.enabled })} /> {strategy.enabled ? 'Enabled' : 'Disabled'}</span>
      </label>
      <p style={{ fontSize: 12, color: 'var(--k-ink-5)' }}>{strategy.description}</p>
    </div>)}
    {mutation.isPending && <p role="status">Saving…</p>}
  </section>;
}

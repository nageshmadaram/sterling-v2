/** Durable informational alert inbox. Only metadata ACK is writable.
 * This module has NO strategy, execution, broker or transport mutations.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { OBSERVABILITY_ROOT } from './contracts';

type AlertState = 'active' | 'acknowledged' | 'resolved' | 'expired';
type Delivery = { state: 'pending' | 'sent' | 'failed' | 'acknowledged' | 'expired';
  attempt: number; last_attempt_at_ms: number; retry_after_ms: number | null;
  receipt: string | null; reason: string };
export type AlertRecord = { alert_id: string; strategy_id: string;
  priority: 'info' | 'warning' | 'critical'; category: string;
  source_system: string; source_type: string; source_ref: string; source_sha256: string;
  run_id: string | null; decision_id: string | null;
  message: string; reason: string; state: AlertState;
  created_at_ms: number; last_event_at_ms: number; audit_ref: string;
  expires_at_ms: number | null; escalated?: boolean;
  delivery: Record<string, Delivery>;
  history: { event_id: string; at_ms: number; kind: string; actor: string;
    reason: string; source_sha256: string }[];
  execution_authorized: false };
export type AlertEnvelope = { source: 'offline_alert_ledger'; read_only: true;
  execution_authorized: false; items: AlertRecord[] };
const POLL = 20_000;
const STALE_MS = 90_000;
const SEVERITY = { critical: 0, warning: 1, info: 2 } as const;

export function validAlertEnvelope(payload: AlertEnvelope | undefined, strategyId: string): boolean {
  return Boolean(payload && payload.read_only === true &&
    payload.execution_authorized === false && payload.source === 'offline_alert_ledger' &&
    Array.isArray(payload.items) && payload.items.every(alert =>
      alert.execution_authorized === false && alert.strategy_id === strategyId &&
      alert.alert_id?.length === 64 &&
      ['info', 'warning', 'critical'].includes(alert.priority) &&
      ['active', 'acknowledged', 'resolved', 'expired'].includes(alert.state) &&
      typeof alert.message === 'string' &&
      typeof alert.audit_ref === 'string' && alert.audit_ref.length === 64 &&
      typeof alert.source_sha256 === 'string' && alert.source_sha256.length === 64 &&
      Array.isArray(alert.history) &&
      alert.history.length > 0 &&
      alert.history.every(row => typeof row.event_id === 'string') &&
      alert.delivery && typeof alert.delivery === 'object' &&
      Object.values(alert.delivery).every(d => d && ['pending','sent','failed','acknowledged','expired'].includes(d.state))));
}

function formatTime(n: number) {
  return Number.isFinite(n) ? new Date(n).toLocaleString('en-IN', {timeZone: 'Asia/Kolkata'}) + ' IST' : 'unknown';
}

export function OfflineAlertInbox({ strategyId }: { strategyId: string }) {
  const client = useQueryClient();
  const [reasonByAlert, setReason] = useState<Record<string, string>>({});
  const base = OBSERVABILITY_ROOT + '/strategies/' + encodeURIComponent(strategyId) + '/alerts';
  const query = useQuery({
    queryKey: ['offline-alert-ledger', strategyId],
    queryFn: () => api.get<AlertEnvelope>(base + '?limit=100'),
    retry: false, refetchInterval: POLL,
  });
  const ack = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<{ changed: boolean; state: 'acknowledged'; execution_authorized: false;
        mutation_scope: 'alert_metadata_only' }>(
        base + '/' + encodeURIComponent(id) + '/acknowledge', { reason }),
    onSuccess: result => {
      if (result.execution_authorized !== false || result.mutation_scope !== 'alert_metadata_only')
        throw new Error('invalid non-authoritative acknowledgement contract');
      void client.invalidateQueries({ queryKey: ['offline-alert-ledger', strategyId] });
    },
  });
  const invalid = query.data && !validAlertEnvelope(query.data, strategyId);
  const stale = query.dataUpdatedAt > 0 && Date.now() - query.dataUpdatedAt > STALE_MS;
  const unavailable = query.isError || invalid || stale;
  const alerts = query.data?.items ?? [];
  const sorted = [...alerts].sort((a,b) => SEVERITY[a.priority] - SEVERITY[b.priority] ||
    b.created_at_ms - a.created_at_ms);
  return <section className="oe-card" aria-label="Durable offline alert inbox" data-testid="offline-durable-alerts">
    <h2>Durable alert inbox · offline only</h2>
    <p className="oe-muted">Signed, tamper-checked ledger · operator acknowledgement records review only; no trading or external message is triggered.</p>
    {query.isPending ? <p role="status">Loading audited offline alerts…</p> :
    unavailable ? <div role="alert" data-testid="offline-alert-untrusted" className="oe-error">
      ALERT EVIDENCE UNAVAILABLE / UNTRUSTED — records withheld. Review the audit ledger and reconnect.</div> :
    !sorted.length ? <p role="status">No durable alerts recorded for this strategy. This is not proof of live system health.</p> :
    <ol className="oe-alert-inbox">{sorted.map(a => <li key={a.alert_id} className="oe-decision">
      <div className="oe-inbox-header">
        <span className={`oe-chip oe-chip--${a.priority === 'info' ? 'neutral' : a.priority}`}>{a.priority}</span>
        <strong>{a.category.replace(/_/g,' ')}</strong>
        <span className="oe-muted">{formatTime(a.created_at_ms)}</span>
        <span className="oe-chip">{a.state}</span>{a.escalated && <span className="oe-chip oe-chip--warning">Escalated</span>}
      </div>
      <p>{a.message}</p>
      <div className="oe-muted">Source: {a.source_system} · {a.source_type} · {a.reason}</div>
      <div className="oe-muted">Delivery: {Object.entries(a.delivery).length ?
        Object.entries(a.delivery).map(([channel, result]) =>
          `${channel}: ${result.state} (attempt ${result.attempt})`).join(' · ') :
          'not dispatched'}</div>
      {a.state === 'active' && <div className="oe-inbox-ack">
        <label htmlFor={`ack-${a.alert_id}`}>Acknowledgement reason</label>
        <input id={`ack-${a.alert_id}`} value={reasonByAlert[a.alert_id] || ''}
          maxLength={256} placeholder="What did you review?"
          onChange={e => setReason(old => ({...old,[a.alert_id]:e.target.value}))} />
        <button type="button" className="oe-button"
          disabled={ack.isPending || (reasonByAlert[a.alert_id] || '').trim().length < 4}
          onClick={() => ack.mutate({id:a.alert_id,reason:reasonByAlert[a.alert_id].trim()})}>
          Acknowledge review
        </button>
      </div>}
      {ack.isError && ack.variables?.id === a.alert_id &&
        <p role="alert">Acknowledgement failed; no state change is assumed.</p>}
      <details><summary>Immutable alert and delivery audit</summary>
        <div className="oe-hash">Source reference: {a.source_ref}</div>
        <div className="oe-hash">Audit hash: {a.audit_ref}</div>
        <ol>{a.history.map(ev => <li key={ev.event_id}>
          {formatTime(ev.at_ms)} · {ev.kind} · {ev.actor}: {ev.reason}
        </li>)}</ol>
      </details>
    </li>)}</ol>}
  </section>;
}

import React from 'react';
import type { EmaTrendCampaign, EmaTrendSupervision } from '../../../types/emaTrend';
import { useEmaTrendOperation } from '../../../hooks/useEmaTrend';

/** Owner-scoped virtual exposure across all profiles, independent of row filters. */
export function EmaTrendOperations({ campaigns, unavailable = false, readOnly = false, supervision, showPause = false, paused }: {
  campaigns: EmaTrendCampaign[]; unavailable?: boolean; readOnly?: boolean; supervision?: EmaTrendSupervision; showPause?: boolean; paused?: boolean;
}) {
  const operation = useEmaTrendOperation();
  const result = operation.data as { status?: string; results?: Array<{ campaign_id?: string; status?: string; reason?: string }> } | undefined;
  const failures = result?.results?.filter(item => item.status === 'UNRESOLVED') || [];
  const [lastOperation, setLastOperation] = React.useState<'pause' | 'resume' | 'flatten' | null>(null);
  const [confirm, setConfirm] = React.useState(false);
  const held = campaigns.filter(c => c.qty > 0 || c.state !== 'CLOSED');
  const shares = held.reduce((sum, c) => sum + c.qty, 0);
  const unitLabel = held.some(c => c.instrument_type && c.instrument_type !== 'EQ') ? 'units' : 'shares';
  const unresolved = held.filter(c => c.pending || c.pending_close || c.state === 'RECOVERY_REQUIRED' || c.state === 'CLOSING').length;
  const disabled = unavailable || readOnly || operation.isPending || held.length === 0;
  React.useEffect(() => { if (disabled) setConfirm(false); }, [disabled]);
  if (readOnly) return null;
  return <section aria-label="EMA Trend paper operations" style={{ padding: '8px 12px', borderBottom: '1px solid var(--k-border)', fontSize: 12 }}>
    <strong>PAPER exposure · all profiles</strong>
    <p style={{ margin: '4px 0' }}>{unavailable ? 'Exposure could not be verified. Refresh before submitting operations.' : `${held.length} active campaigns · ${shares} confirmed ${unitLabel} · ${unresolved} pending or unresolved campaigns`}</p>
    <p style={{ margin: '4px 0' }}>Supervision: {unavailable ? 'UNAVAILABLE' : supervision?.status || 'UNKNOWN'}{!unavailable && supervision?.age_ms != null ? ` · last completed check ${Math.floor(supervision.age_ms / 1000)}s ago` : ' · no completed check recorded'}</p>
    {held.length > 0 && (unavailable || supervision?.status !== 'RUNNING') && <p role="alert">Paper supervision needs attention. Inspect remaining shares and pending orders; stop coverage is not guaranteed.</p>}
    {supervision?.errors.map((error, index) => <p role="alert" key={index}>{error.campaign_id ? `${error.campaign_id}: ` : ''}{error.reason}</p>)}
    <p style={{ margin: '4px 0' }}>Protection: PAPER MONITOR ONLY. Keep the backend and quote feed running.</p>
    {showPause && <button type="button" className="sb-tool" disabled={unavailable || readOnly || operation.isPending || paused == null} onClick={() => { const next = paused ? 'resume' : 'pause'; setLastOperation(next); operation.mutate(next); }}>{paused ? 'Resume EMA paper entries' : 'Pause EMA paper entries'}</button>}
    {!confirm ? <button type="button" className="sb-tool" disabled={disabled} onClick={() => setConfirm(true)}>Close all EMA paper campaigns</button> : <>
      <span>Pause new entries and request closure of every owned EMA paper campaign? </span>
      <button type="button" className="sb-tool" disabled={disabled} onClick={() => {
        setConfirm(false); setLastOperation('flatten'); operation.mutate('flatten');
      }}>Confirm paper close all</button>
      <button type="button" className="sb-tool" onClick={() => setConfirm(false)}>Keep campaigns</button>
    </>}
    {operation.isPending && <p role="status">Submitting EMA paper operation…</p>}
    {operation.isSuccess && <p role="status">{lastOperation === 'pause' ? 'EMA paper entries paused; held campaigns continue to be managed.' : lastOperation === 'resume' ? 'EMA paper pause released; server admission checks still apply.' : 'Closure requests recorded; entries are paused. Inspect remaining shares and unresolved orders. A request does not confirm closure.'}</p>}
    {failures.map((item, index) => <p role="alert" key={item.campaign_id || index}>Unresolved {item.campaign_id || 'campaign'}: {item.reason || 'Inspect pending orders and remaining shares.'}</p>)}
    {operation.error && <p role="alert">Paper operation failed: {(operation.error as Error).message}. Inspect campaigns before another request.</p>}
  </section>;
}

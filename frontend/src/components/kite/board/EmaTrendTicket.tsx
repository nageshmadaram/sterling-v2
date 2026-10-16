import React from 'react';
import type { BoardSignal } from './boardTypes';
import { useEmaTrendCampaign, useEmaTrendCampaignAction, useEmaTrendExecution } from '../../../hooks/useEmaTrend';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { fmtMoney, fmtPx } from '../../../lib/format';
import { emaTrendCampaignsToBoard } from './emaTrendAdapter';

/** Cash shares and strategy-owned paper actions. This never opens the generic order window. */
export function EmaTrendTicket({ signal: initialSignal, readOnly = false }: { signal: BoardSignal; readOnly?: boolean }) {
  const replay = useSimNowMs() !== null;
  const campaign = useEmaTrendCampaign(initialSignal.emaTrend?.campaignId, !readOnly && !initialSignal.historical);
  const signal = campaign.data?.campaign ? emaTrendCampaignsToBoard([campaign.data.campaign])[0] : initialSignal;
  const execute = useEmaTrendExecution();
  const action = useEmaTrendCampaignAction();
  const meta = signal.emaTrend;
  const historical = readOnly || replay || signal.historical;
  const busy = execute.isPending || action.isPending;
  const canEnter = !historical && Boolean(meta?.signalId) && signal.status === 'armed' && signal.executionEligible === true;
  const canReduce = !historical && !campaign.isError && Boolean(meta?.campaignId) && !meta?.pending && (signal.sizing.quantity || 0) > 0 && meta?.campaignState !== 'CLOSED';
  // Pending entry/exit orders must not disable a full close request.
  const canClose = !historical && !campaign.isError && Boolean(meta?.campaignId) && meta?.stateRevision != null && (meta?.campaignState !== 'CLOSED' || meta?.pending) && !meta?.flattenRequested;
  return <div style={{ padding: '10px 12px', fontSize: 12, lineHeight: 1.6 }}>
    <div><strong>PAPER · CASH EQUITY</strong> · {signal.instrument.exchange}:{signal.instrument.symbol}</div>
    <div>Shares: {signal.sizing.quantity ?? 'Sized by server'} · Entry: {fmtPx(signal.levels.entry)} · Stop: {fmtPx(signal.levels.trail ?? signal.levels.stop)} · Capital: {fmtMoney(signal.sizing.deployedInr)}</div>
    <p style={{ margin: '5px 0' }}>{historical ? 'Historical evidence is read-only.' : signal.reason}</p>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {meta?.signalId && <button type="button" className="sb-tool" disabled={!canEnter || busy}
        onClick={() => { if (canEnter) execute.mutate({ signalId: meta.signalId!, configRevision: meta.configRevision }); }}>
        {execute.isPending ? 'Publishing paper plan…' : 'Execute paper plan'}
      </button>}
      {meta?.campaignId && <>
        <button type="button" className="sb-tool" disabled={!canReduce || busy}
          onClick={() => { if (canReduce) action.mutate({ campaignId: meta.campaignId!, revision: meta.stateRevision!, action: 'reduce', percent: 25 }); }}>Reduce 25% paper</button>
        <button type="button" className="sb-tool" disabled={!canClose || busy}
          onClick={() => { if (canClose) action.mutate({ campaignId: meta.campaignId!, revision: meta.stateRevision!, action: 'close' }); }}>Close paper campaign</button>
      </>}
    </div>
    {(execute.error || action.error) && <p role="alert">{((execute.error || action.error) as Error).message}</p>}
    {campaign.error && <p role="alert">Campaign refresh failed: {(campaign.error as Error).message}. New actions are unavailable until state is verified.</p>}
    {execute.isSuccess && <p role="status">Paper request recorded. Confirmed fills and protection appear in the campaign; submission alone is not a fill.</p>}
    {action.isSuccess && <p role="status">{action.data?.status === 'UNRESOLVED' ? 'Close requested; order reconciliation is still pending. Do not assume the position is flat.' : 'Paper action recorded. Confirmed inventory updates after reconciliation.'}</p>}
  </div>;
}

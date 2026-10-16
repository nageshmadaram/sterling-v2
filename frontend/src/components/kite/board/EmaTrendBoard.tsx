import React from 'react';
import { useEmaTrendConfig, useEmaTrendSnapshot, useRunEmaTrendScan, useEmaTrendScan, useCancelEmaTrendScan, useEmaTrendOperation } from '../../../hooks/useEmaTrend';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from './emaTrendAdapter';
import type { BoardSignal } from './boardTypes';
import { SignalBoard } from './SignalBoard';
import { BoardFilters } from './BoardFilters';
import { useBoardView } from './useBoardView';
import { useSignalColumns } from './useSignalColumns';
import { EmaTrendReplay } from './EmaTrendReplay';
import { EmaTrendTicket } from './EmaTrendTicket';
import { StrategyBoardFooter } from './StrategyBoardFooter';
import { openSettingsSection } from '../config/registry';
import { EMA_TREND_MODE_LABEL, type EmaTrendMode } from '../../../types/emaTrend';

export function EmaTrendBoard({ onOpenDetail, onOpenChart }: {
  onOpenDetail?: (signal: BoardSignal) => void;
  onOpenChart?: (quoteKey: string, mode: EmaTrendMode, signal: BoardSignal) => void;
}) {
  const replay = useSimNowMs() !== null;
  const config = useEmaTrendConfig();
  const snapshot = useEmaTrendSnapshot();
  const scan = useRunEmaTrendScan();
  const job = useEmaTrendScan(scan.data?.id || snapshot.data?.scan?.id);
  const cancel = useCancelEmaTrendScan();
  const operation = useEmaTrendOperation();
  const common = useSignalColumns();
  const [showReplay, setShowReplay] = React.useState(false);
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [selectedMode, setSelectedMode] = React.useState<EmaTrendMode | ''>('');
  const signals = React.useMemo(() => replay ? [] : [
    ...emaTrendCampaignsToBoard(snapshot.data?.campaigns || []),
    ...emaTrendSetupsToBoard(snapshot.data?.rows || []).map(row => ({ ...row, executionEligible: row.executionEligible && row.emaTrend?.configRevision === snapshot.data?.revision && snapshot.data?.config.enabled === true && snapshot.data.paused !== true && snapshot.data.capabilities?.paper === true })),
  ].filter(signal => !selectedMode || signal.emaTrend?.mode === selectedMode), [snapshot.data, replay, selectedMode]);
  const view = useBoardView(signals, { storageKey: 'ema_trend', endedByDefault: true });
  // Held campaigns remain visible even when their last decision is from a previous session.
  const visible = [...view.visible];
  for (const signal of signals) if (signal.emaTrend?.campaignId && signal.status !== 'ended' && !visible.some(row => row.id === signal.id)) visible.unshift(signal);
  const scanning = ['QUEUED', 'RUNNING'].includes(job.data?.status || '');
  React.useEffect(() => { if (job.data?.status === 'COMPLETED') void snapshot.refetch(); }, [job.data?.id, job.data?.status]);
  const disabled = !config.data?.config.enabled || config.isError || replay;
  return <div className="sb-strategy-layout" style={{ height: '100%', overflow: 'auto' }}>
    <div className="sb-strategy-rows">
      <BoardFilters hideSearch view={{ ...view, ...common }} columns={common.columns}>
        <StrategyBoardFooter name="EMA Trend" onReset={() => { setSelectedMode(''); setOpenId(null); }}>
          <div>
            <button type="button" className="sb-tool" disabled={disabled || scanning || scan.isPending} onClick={() => scan.mutate(undefined)}>{scanning || scan.isPending ? 'Scanning…' : 'Scan now'}</button>
            {scanning && <button type="button" className="sb-tool" disabled={cancel.isPending} onClick={() => { if (job.data?.id) cancel.mutate(job.data.id); }}>Stop scan</button>}
            <button type="button" className="sb-tool" aria-pressed={showReplay} onClick={() => setShowReplay(value => !value)}>Recorded replay</button>
            <button type="button" className="sb-tool" onClick={() => openSettingsSection('emaTrend')}>EMA Trend settings</button>
            <button type="button" className="sb-tool" disabled={replay || operation.isPending} onClick={() => operation.mutate(snapshot.data?.paused ? 'resume' : 'pause')}>{snapshot.data?.paused ? 'Resume paper entries' : 'Pause paper entries'}</button>
            <button type="button" className="sb-tool" disabled={replay || operation.isPending}
              onClick={() => {
                if (window.confirm('Pause new EMA paper entries and request closure of all EMA paper campaigns? Pending orders may remain unresolved until reconciliation.')) operation.mutate('flatten');
              }}>Flatten all paper</button>
            <span>PAPER ONLY · {snapshot.data?.paused ? 'entries paused' : config.data?.config.enabled ? 'scanner enabled' : 'scanner off'}</span>
            <label>View profile <select aria-label="EMA Trend view profile" value={selectedMode} onChange={event => setSelectedMode(event.target.value as EmaTrendMode | '')}>
              <option value="">All configured profiles</option>{Object.entries(EMA_TREND_MODE_LABEL).map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}
            </select></label>
          </div>
          <p>EMA10/21 and DEMA50 with Supertrend (10,3). Shares and campaigns use confirmed paper fills. Economic evidence is inconclusive; live orders are disabled.</p>
          {(snapshot.data?.blockers || config.data?.blockers || []).map(reason => <p key={reason}>{reason.replace(/_/g, ' ')}</p>)}
        </StrategyBoardFooter>
      </BoardFilters>
      {showReplay && <EmaTrendReplay />}
      {replay && <p role="status" style={{ padding: 12 }}>Historical replay is read-only. Run the EMA Trend replay from its recorded dataset; the live paper snapshot is not shown in this view.</p>}
      {operation.isSuccess && operation.variables === 'flatten' && <p role="status">Flatten requested. Verify each campaign and pending order; this is not confirmation of a completed close.</p>}
      {[snapshot.error, config.error, scan.error, cancel.error, operation.error].filter(Boolean).map((error, index) => <p role="alert" key={index} style={{ padding: 12 }}>{(error as Error).message}</p>)}
      {job.data?.status === 'FAILED' && <p role="alert">Scan failed: {(job.data.errors || []).join(' · ')}</p>}
      {!replay && snapshot.isLoading && <p style={{ padding: 12 }}>Loading EMA Trend…</p>}
      {!replay && !snapshot.isLoading && !signals.length && !snapshot.error && <p style={{ padding: 12 }}>No completed-bar setups or held campaigns. Configure equities and paper allocation in Connect, then enable and scan.</p>}
      <SignalBoard signals={visible} columns={common.columns} hidden={common.hidden} onReorderColumn={common.onReorderColumn} rowScroll={common.rowScroll}
        isHistoricalSim={replay} strategyDetailsInExpansion openId={openId} onToggle={id => setOpenId(previous => previous === id ? null : id)} onOpenDetail={onOpenDetail}
        renderTrade={signal => signal.emaTrend?.signalId ? <button className="sb-tool" type="button" disabled={replay || signal.status !== 'armed' || !signal.executionEligible} onClick={() => setOpenId(signal.id)}>Paper</button> : null}
        renderChart={signal => onOpenChart && <button type="button" className="sb-tool" disabled={replay || signal.historical} aria-label={`Chart ${signal.instrument.symbol}`} onClick={() => onOpenChart(signal.instrument.quoteKey!, signal.emaTrend!.mode, signal)}>↗</button>}
        renderDetail={signal => <EmaTrendTicket signal={signal} readOnly={replay} />} />
    </div>
  </div>;
}

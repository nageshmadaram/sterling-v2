import React from 'react';
import { useEmaTrendConfig, useEmaTrendSnapshot, useRunEmaTrendScan, useEmaTrendScan, useCancelEmaTrendScan, useEmaTrendOperation } from '../../../hooks/useEmaTrend';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from './emaTrendAdapter';
import type { BoardSignal } from './boardTypes';
import { SignalBoard } from './SignalBoard';
import { BoardFilters } from './BoardFilters';
import { useBoardView } from './useBoardView';
import { useSignalColumns } from './useSignalColumns';
import { EmaTrendTicket } from './EmaTrendTicket';
import { StrategyBoardFooter } from './StrategyBoardFooter';
import { openSettingsSection } from '../config/registry';
import { EMA_TREND_MODE_LABEL, type EmaTrendMode } from '../../../types/emaTrend';

const MODES: EmaTrendMode[] = ['swing', 'intraday', 'scalping', 'ultra_scalping'];
const AUTO_SCAN_INTERVAL_MS = 60_000;

/** One operator view of completed-bar evidence; changing tabs never arms an order. */
export function EmaTrendBoard({ onOpenDetail, onOpenChart }: {
  onOpenDetail?: (signal: BoardSignal) => void;
  onOpenChart?: (quoteKey: string, mode: EmaTrendMode, signal: BoardSignal) => void;
}) {
  const historical = useSimNowMs() !== null;
  const config = useEmaTrendConfig();
  const [selectedMode, setSelectedMode] = React.useState<EmaTrendMode | ''>('');
  // The query itself is mode-scoped, not merely client-filtered: one active
  // profile cannot have its rows pushed off the 1000-row snapshot page by
  // historical updates in other profiles.
  const snapshot = useEmaTrendSnapshot(selectedMode || undefined);
  const scan = useRunEmaTrendScan();
  const job = useEmaTrendScan(scan.data?.id || snapshot.data?.scan?.id);
  const cancel = useCancelEmaTrendScan();
  const operation = useEmaTrendOperation();
  const common = useSignalColumns();
  const [openId, setOpenId] = React.useState<string | null>(null);
  const lastAutoRequest = React.useRef<{ key: string; at: number } | null>(null);

  const activeModes = config.data?.config.modes || [];
  React.useEffect(() => {
    if (selectedMode && config.data && !activeModes.includes(selectedMode)) setSelectedMode('');
  }, [selectedMode, config.data?.revision]);
  const hasSymbols = Boolean(config.data?.config.symbols.length);
  const scannerEnabled = Boolean(config.data?.config.enabled);
  const scanning = ['QUEUED', 'RUNNING'].includes(job.data?.status || snapshot.data?.scan?.status || '');
  const lastScan = snapshot.data?.scan;
  const autoScanReady = !historical && scannerEnabled && hasSymbols && !config.isError &&
    !config.isLoading && !snapshot.isLoading && !snapshot.isError && !scanning &&
    !scan.isPending && config.data != null && activeModes.length > 0;
  const lastScanAt = lastScan?.finished_ms || lastScan?.created_ms || 0;

  // React Query refreshes snapshots and active jobs. This effect requests
  // actual completed-bar scans after setup and on a bounded cadence; market
  // state or a changed tab never duplicates a queued scan.
  React.useEffect(() => {
    if (!autoScanReady || !config.data) return;
    const now = Date.now();
    const key = `${config.data.revision}:${activeModes.join(',')}`;
    if (lastAutoRequest.current?.key === key && now - lastAutoRequest.current.at < AUTO_SCAN_INTERVAL_MS) return;
    if (lastScanAt && now - lastScanAt < AUTO_SCAN_INTERVAL_MS) return;
    lastAutoRequest.current = { key, at: now };
    scan.mutate(activeModes);
  }, [autoScanReady, config.data?.revision, activeModes.join(','), lastScanAt, snapshot.data?.generated_ms]);

  React.useEffect(() => {
    if (job.data?.status === 'COMPLETED' || job.data?.status === 'FAILED') void snapshot.refetch();
  }, [job.data?.id, job.data?.status]);

  const signals = React.useMemo(() => historical ? [] : [
    ...emaTrendCampaignsToBoard(snapshot.data?.campaigns || []),
    ...emaTrendSetupsToBoard(snapshot.data?.rows || []).map(row => ({
      ...row,
      executionEligible: row.executionEligible &&
        row.emaTrend?.configRevision === snapshot.data?.revision &&
        snapshot.data?.config.enabled === true &&
        snapshot.data.paused !== true && snapshot.data.capabilities?.paper === true,
    })),
  ], [snapshot.data, historical]);
  const view = useBoardView(signals, { storageKey: 'ema_trend_profiles_v2', endedByDefault: true });
  // Never let a view filter hide an active or unresolved held campaign.
  const visible = [...view.visible];
  for (const signal of signals) if (signal.emaTrend?.campaignId && signal.status !== 'ended' && !visible.some(row => row.id === signal.id)) visible.unshift(signal);
  const canScan = !historical && scannerEnabled && hasSymbols && !scanning && !scan.isPending;
  const scanError = (job.data?.errors || []).map(error => typeof error === 'string' ? error : (error as { reason?: string; symbol?: string; mode?: string }).reason || 'Unknown scan failure');
  const status = historical ? 'Historical view — trading disabled' : scanning ? 'Scanning completed bars…' :
    !hasSymbols ? 'Add NSE/BSE equities in EMA Settings to start scanning' :
    !scannerEnabled ? 'Scanner off — enable EMA in Settings' :
    lastScan?.status === 'FAILED' ? 'Last scan failed — see details below' :
    'Scanning automatically · refreshes every 10s';

  return <div className="sb-strategy-layout" style={{ height: '100%', overflow: 'auto' }}>
    <div className="sb-strategy-rows">
      <div role="group" aria-label="EMA Trend profiles" style={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5, padding: '7px 10px',
        borderBottom: '1px solid var(--k-border)', background: 'var(--k-bg)', position: 'relative',
      }}>
        <button type="button" className="sb-tool" aria-pressed={!selectedMode}
          onClick={() => { setSelectedMode(''); setOpenId(null); }}>All</button>
        {MODES.map(mode => <button key={mode} type="button" className="sb-tool"
          aria-pressed={selectedMode === mode}
          title={activeModes.includes(mode) ? `Show ${EMA_TREND_MODE_LABEL[mode]} signals` : `Enable ${EMA_TREND_MODE_LABEL[mode]} in EMA Settings`}
          disabled={!activeModes.includes(mode)}
          onClick={() => { setSelectedMode(mode); setOpenId(null); }}>
          {EMA_TREND_MODE_LABEL[mode]}
        </button>)}
        <span role="status" style={{ fontSize: 10, opacity: 0.8, marginLeft: 'auto' }}>{status}</span>
      </div>
      <BoardFilters hideSearch view={{ ...view, ...common,
        offers: { ...view.offers, best: false } }} columns={common.columns}>
        <StrategyBoardFooter name="EMA Trend" onReset={() => { setSelectedMode(''); setOpenId(null); }}>
          <div>
            <button type="button" className="sb-tool" disabled={!canScan} onClick={() => scan.mutate(activeModes)}>
              {scanning || scan.isPending ? 'Scanning…' : 'Refresh signals'}</button>
            {scanning && <button type="button" className="sb-tool" disabled={cancel.isPending}
              onClick={() => { if (job.data?.id) cancel.mutate(job.data.id); }}>Stop scan</button>}
            <button type="button" className="sb-tool" onClick={() => openSettingsSection('emaTrend')}>EMA Trend settings</button>
            <button type="button" className="sb-tool" disabled={historical || operation.isPending || !signals.some(row => row.emaTrend?.campaignId && row.status !== 'ended')}
              onClick={() => {
                if (window.confirm('Request closure of all EMA simulation campaigns? This does NOT close any real Kite position, and unsettled orders still need reconciliation.')) operation.mutate('flatten');
              }}>Emergency close (simulation)</button>
            <span>Execution: simulation · live orders unavailable</span>
          </div>
          <p>10/21 EMA · DEMA50 · ST(10,3). Signals use completed market candles. Status is observational, not an order recommendation.</p>
        </StrategyBoardFooter>
      </BoardFilters>
      {historical && <p role="status" style={{ padding: 12 }}>Historical view is read-only. Return to the current session to see refreshed EMA signals.</p>}
      {operation.isSuccess && operation.variables === 'flatten' &&
        <p role="status" style={{ padding: 12 }}>EMA simulation close requested. Check outstanding orders and confirmed shares; this does not close real Kite positions.</p>}
      {[snapshot.error, config.error, scan.error, cancel.error, operation.error].filter(Boolean).map((error, index) =>
        <p role="alert" key={index} style={{ padding: 12 }}>{(error as Error).message}</p>)}
      {job.data?.status === 'FAILED' && <p role="alert">EMA scan failed: {scanError.join(' · ')}</p>}
      {!historical && snapshot.isLoading && <p style={{ padding: 12 }}>Loading EMA Trend signals…</p>}
      {!historical && !snapshot.isLoading && !signals.length && !snapshot.error &&
        <p role="status" style={{ padding: 12 }}>
          {!hasSymbols ? 'No equities selected. Open EMA Trend settings and add NSE/BSE cash-equity symbols.' :
            !scannerEnabled ? 'EMA scanner is off. Enable it in Settings to refresh signals.' :
            scanning ? 'Analyzing completed bars for the configured equities…' :
            'No completed-bar evidence yet for this profile. The scanner will keep checking; review scan errors if this persists.'}
        </p>}
      {visible.length > 0 ? <SignalBoard signals={visible} columns={common.columns} hidden={common.hidden} onReorderColumn={common.onReorderColumn} rowScroll={common.rowScroll}
        isHistoricalSim={historical} strategyDetailsInExpansion openId={openId}
        onToggle={id => setOpenId(previous => previous === id ? null : id)}
        onOpenDetail={onOpenDetail}
        renderTrade={signal => signal.emaTrend?.signalId ? <button className="sb-tool" type="button"
          disabled={historical || signal.status !== 'armed' || !signal.executionEligible}
          onClick={() => setOpenId(signal.id)}>Preview</button> : null}
        renderChart={signal => onOpenChart && <button type="button" className="sb-tool"
          disabled={historical || signal.historical} aria-label={`Chart ${signal.instrument.symbol}`}
          onClick={() => onOpenChart(signal.instrument.quoteKey!, signal.emaTrend!.mode, signal)}>↗</button>}
        renderDetail={signal => <EmaTrendTicket signal={signal} readOnly={historical} />}
      /> : signals.length > 0 ? <p role="status" style={{ padding: 12 }}>No rows match the current filters. Reset the EMA view to show all recorded signals.</p> : null}
    </div>
  </div>;
}

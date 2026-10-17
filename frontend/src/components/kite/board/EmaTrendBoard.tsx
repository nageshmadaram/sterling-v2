import React from 'react';
import { useEmaTrendConfig, useEmaTrendSnapshot, useRunEmaTrendScan, useEmaTrendScan, useCancelEmaTrendScan, useEmaTrendOperation, useUpdateEmaTrend } from '../../../hooks/useEmaTrend';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from './emaTrendAdapter';
import type { BoardSignal } from './boardTypes';
import { SignalBoard } from './SignalBoard';
import { BoardFilters, ColumnsMenu } from './BoardFilters';
import { useBoardView } from './useBoardView';
import { useSignalColumns } from './useSignalColumns';
import { EmaTrendTicket } from './EmaTrendTicket';
import { EmaTrendOperations } from './EmaTrendOperations';
import { openSettingsSection } from '../config/registry';
import { EMA_TREND_MODE_LABEL, type EmaTrendMode } from '../../../types/emaTrend';
import { EMA_TREND_QUICKSTART } from '../../../config/emaTrendPreset';

const MODES: EmaTrendMode[] = ['swing', 'intraday', 'scalping', 'ultra_scalping'];
const AUTO_SCAN_INTERVAL_MS = 60_000;

/** Completed-bar evidence view; selecting profiles never arms a broker order. */
export function EmaTrendBoard({ onOpenDetail, onOpenChart }: {
  onOpenDetail?: (signal: BoardSignal) => void;
  onOpenChart?: (quoteKey: string, mode: EmaTrendMode, signal: BoardSignal) => void;
}) {
  const historical = useSimNowMs() !== null;
  const config = useEmaTrendConfig();
  // Profiles is a multi-select *view*, independent of enabled scan modes.
  // Each selected profile has its own server-filtered page, so high-volume
  // histories from one mode cannot starve another.
  const [selectedModes, setSelectedModes] = React.useState<EmaTrendMode[]>([...MODES]);
  const snapshot = useEmaTrendSnapshot(selectedModes);
  const scan = useRunEmaTrendScan();
  // Prefer the just-requested job over a stale last-scan id from a snapshot.
  // Otherwise the UI can claim an old scan is complete while a new one runs.
  const job = useEmaTrendScan(scan.data?.id || snapshot.data?.scan?.id);
  const cancel = useCancelEmaTrendScan();
  const operation = useEmaTrendOperation();
  const quickstart = useUpdateEmaTrend();
  const common = useSignalColumns();
  const [openId, setOpenId] = React.useState<string | null>(null);
  const lastAutoRequest = React.useRef<{ key: string; at: number } | null>(null);

  const activeModes = config.data?.config.modes || [];
  const hasSymbols = Boolean(config.data?.config.symbols.length);
  const scannerEnabled = Boolean(config.data?.config.enabled);
  const scanning = ['QUEUED', 'RUNNING'].includes(job.data?.status || '') ||
    ['QUEUED', 'RUNNING'].includes(snapshot.data?.scan?.status || '') || scan.isPending;
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
  // v4 starts with an unrestricted view; stale TODAY/ENDED preferences must
  // not make genuine older saved signals look like a broken scanner.
  const view = useBoardView(signals, { storageKey: 'ema_trend_profiles_v4', endedByDefault: true });
  // Never let a view filter hide an active or unresolved held campaign.
  const visible = [...view.visible];
  for (const signal of signals) if (signal.emaTrend?.campaignId && signal.status !== 'ended' && !visible.some(row => row.id === signal.id)) visible.unshift(signal);
  const canScan = !historical && scannerEnabled && hasSymbols && !scanning && !scan.isPending;
  const startReadOnlyScanner = () => {
    if (historical || !config.data || config.data.config.symbols.length ||
        quickstart.isPending) return;
    // Explicit user action; 5 cash stocks × 4 profiles. Never enable paper
    // auto-entry, submit a trade, or silently overwrite an existing universe.
    quickstart.mutate({
      expected_revision: config.data.revision,
      changes: {
        symbols: [...EMA_TREND_QUICKSTART],
        modes: [...MODES],
        enabled: true,
        auto_execute: false,
      },
    });
  };
  const selectedEnabled = selectedModes.filter(mode => activeModes.includes(mode));
  const unavailableSelected = selectedModes.filter(mode => !activeModes.includes(mode));
  const toggleMode = (mode: EmaTrendMode) => {
    setSelectedModes(previous => previous.includes(mode)
      ? previous.length > 1 ? previous.filter(value => value !== mode) : previous
      : MODES.filter(value => previous.includes(value) || value === mode));
    setOpenId(null);
  };
  const resetProfiles = () => { setSelectedModes([...MODES]); setOpenId(null); };
  const currentScan = job.data || snapshot.data?.scan || null;
  const scanError = (currentScan?.errors || []).map(error => {
    if (typeof error === 'string') return error;
    return [error.mode, error.symbol, error.reason || 'Unknown scan failure'].filter(Boolean).join(' · ');
  });
  const scanResults = currentScan?.results || [];
  const processedPairs = scanResults.length;
  const providerBars = scanResults.reduce((sum, result) => sum + result.bars, 0);
  const safetyBlockers = (config.data?.blockers || [])
    .filter((reason, index, all) => all.indexOf(reason) === index);
  const status = historical ? 'Historical view — trading disabled' : scanning ? 'Scanning completed bars…' :
    !hasSymbols ? 'No instruments configured — choose a starter scan or open Settings' :
    selectedEnabled.length === 0 ? 'Enable a selected profile in EMA Settings to scan it' :
    !scannerEnabled ? 'Scanner off — enable EMA in Settings' :
    lastScan?.status === 'FAILED' ? 'Scan failed — review details' :
    `${signals.length} saved records · auto-refresh every 10s`;

  return <div className="sb-strategy-layout" style={{ height: '100%', overflow: 'auto' }}>
    <div className="sb-strategy-rows">
      <BoardFilters hideSearch view={{ ...view, ...common,
        offers: { ...view.offers, best: false } }} columns={common.columns}>
        <ColumnsMenu buttonText="EMA TREND" menuLabel="EMA Trend visible profiles"
          buttonTitle="Filter EMA Trend signals by timeframe; this does not change scanning or orders"
          showAllText="Select all profiles"
          items={MODES.map(mode => ({
            id: mode, label: EMA_TREND_MODE_LABEL[mode],
            on: selectedModes.includes(mode),
            toggle: () => toggleMode(mode),
          }))}
          onShowAll={resetProfiles} />
        <span aria-label="Selected EMA Trend profiles" style={{ fontSize: 10, opacity: 0.9 }}>
          {selectedModes.length === MODES.length ? 'All profiles' :
            selectedModes.map(mode => EMA_TREND_MODE_LABEL[mode]).join(' · ')}
        </span>
        <button type="button" className="sb-tool" disabled={!canScan}
          onClick={() => scan.mutate(activeModes)}>
          {scanning ? 'Scanning…' : 'Refresh signals'}
        </button>
        {scanning && <button type="button" className="sb-tool"
          disabled={cancel.isPending || !job.data?.id}
          onClick={() => { if (job.data?.id) cancel.mutate(job.data.id); }}>Stop scan</button>}
        <button type="button" className="sb-tool"
          onClick={() => openSettingsSection('emaTrend')}>Settings</button>
        <span role="status" style={{ fontSize: 10, opacity: 0.85 }}>{status}</span>
        {signals.some(row => row.emaTrend?.campaignId && row.status !== 'ended') &&
          <button type="button" className="sb-tool" disabled={historical || operation.isPending}
            onClick={() => {
              if (window.confirm('Request closure of all EMA simulated campaigns? This will not close real Kite positions. Verify pending fills and reconciliation.')) operation.mutate('flatten');
            }}>Close simulated campaigns</button>}
        <details style={{ fontSize: 11, maxWidth: '100%' }}>
          <summary style={{ cursor: 'pointer' }}>
            Live trading blocked · {safetyBlockers.length} safety checks
          </summary>
          <div style={{ padding: '8px 10px', lineHeight: 1.5, maxWidth: 440 }}>
            <strong>Execution: simulation only. Broker orders are unavailable.</strong>
            {safetyBlockers.map(reason => <div key={reason}>{reason.replace(/_/g, ' ')}</div>)}
            <p>EMA10/21 · DEMA50 · Supertrend (10,3). The strategy uses completed candles and is not live-certified.</p>
          </div>
        </details>
      </BoardFilters>
      {!historical && (snapshot.data?.campaigns?.some(c =>
        c.qty > 0 || Boolean(c.pending || c.pending_close) || c.state === 'RECOVERY_REQUIRED') ||
        (snapshot.data?.supervision && ['STALE', 'DEGRADED'].includes(snapshot.data.supervision.status))) &&
        <EmaTrendOperations
          campaigns={snapshot.data?.campaigns || []}
          supervision={snapshot.data?.supervision}
          unavailable={snapshot.isLoading || snapshot.isError}
          readOnly={historical} />}
      {historical && <p role="status" style={{ padding: 12 }}>Historical view is read-only. Return to the current session to see refreshed EMA signals.</p>}
      {operation.isSuccess && operation.variables === 'flatten' &&
        <p role="status" style={{ padding: 12 }}>EMA simulation close requested. Check outstanding orders and confirmed shares; this does not close real Kite positions.</p>}
      {[snapshot.error, config.error, scan.error, cancel.error, operation.error, quickstart.error].filter(Boolean).map((error, index) =>
        <p role="alert" key={index} style={{ padding: 12 }}>{(error as Error).message}</p>)}
      {(currentScan?.status === 'FAILED' || scanError.length > 0) && scanError.length > 0 &&
        <p role="alert" style={{ padding: 12 }}>EMA scan errors: {scanError.slice(0, 8).join(' · ')}</p>}
      {snapshot.data?.failedModes?.length ? <p role="alert" style={{ padding: 12 }}>
        Could not load saved signals for {snapshot.data.failedModes.map(mode => EMA_TREND_MODE_LABEL[mode]).join(', ')}.
        Other profiles remain visible. Check the EMA API and refresh.
      </p> : null}
      {!historical && snapshot.isLoading && <p style={{ padding: 12 }}>Loading EMA Trend signals…</p>}
      {!historical && !snapshot.isLoading && !hasSymbols && !config.isLoading && !config.isError &&
        <div role="region" aria-label="Set up EMA Trend signals" style={{ padding: 14, display: 'grid', gap: 8,
          border: '1px solid var(--k-border)', borderRadius: 8, margin: 10 }}>
          <strong>No instruments selected for EMA Trend.</strong>
          <span>Choose exact stocks or listed contracts in Settings, or start with five NSE stocks across all four profiles.
            This enables read-only scans, not automatic paper entries or real orders.</span>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="sb-tool" type="button" onClick={startReadOnlyScanner}
              disabled={quickstart.isPending || !config.data}>
              {quickstart.isPending ? 'Configuring scanner…' : 'Start scanning 5 NSE stocks · All profiles'}
            </button>
            <button className="sb-tool" type="button" onClick={() => openSettingsSection('emaTrend')}>
              Choose instruments
            </button>
          </div>
        </div>}
      {!historical && !snapshot.isLoading && !signals.length && !snapshot.error && hasSymbols &&
        <p role="status" style={{ padding: 12 }}>
          {selectedEnabled.length === 0 ? 'None of the selected profiles is enabled for scanning. Enable a profile in EMA Trend settings.' :
            !scannerEnabled ? 'EMA scanner is off. Enable it in Settings to refresh signals.' :
            scanning ? 'Analyzing completed bars for the configured instruments…' :
            currentScan?.status === 'FAILED' ? 'Latest scan failed. Check the scan errors above; no valid signals have been recorded.' :
            currentScan?.status === 'IDLE' || !currentScan?.id ? 'No completed scan is recorded. Run Refresh signals or keep the automatic scanner enabled.' :
            scanResults.length > 0
              ? `Last scan processed ${processedPairs} symbol/profile pairs and ${providerBars} source candles, but no setup records are available for these profiles. Review scanner errors or data gaps.`
              : 'No saved setup evidence yet. The scanner monitors completed candles; no signals have been fabricated.'}
        </p>}
      {snapshot.data?.truncatedModes?.length ? <p role="status" style={{ padding: 12 }}>
        Showing the newest 1,000 rows per profile for {snapshot.data.truncatedModes.map(mode => EMA_TREND_MODE_LABEL[mode]).join(', ')}. Historical rows remain in the server archive.
      </p> : null}
      {unavailableSelected.length > 0 && signals.length > 0 &&
        <p role="status" style={{ padding: '4px 12px', fontSize: 11 }}>
          Not enabled for new scans: {unavailableSelected.map(mode => EMA_TREND_MODE_LABEL[mode]).join(', ')}. Any existing history is still shown.
        </p>}
      {visible.length > 0 ? <SignalBoard signals={visible} columns={common.columns} hidden={common.hidden} onReorderColumn={common.onReorderColumn} rowScroll={common.rowScroll}
        isHistoricalSim={historical} strategyDetailsInExpansion openId={openId}
        onToggle={id => setOpenId(previous => previous === id ? null : id)}
        onOpenDetail={onOpenDetail}
        renderTrade={signal => signal.emaTrend?.signalId ? <button className="sb-tool" type="button"
          disabled={historical || signal.status !== 'armed' || !signal.executionEligible}
          onClick={event => { event.stopPropagation(); setOpenId(signal.id); }}>Preview</button> : null}
        renderChart={signal => onOpenChart && <button type="button" className="sb-tool"
          disabled={historical || signal.historical} aria-label={`Chart ${signal.instrument.symbol}`}
          onClick={event => { event.stopPropagation(); onOpenChart(signal.instrument.quoteKey!, signal.emaTrend!.mode, signal); }}>↗</button>}
        renderDetail={signal => <EmaTrendTicket signal={signal} readOnly={historical} />}
      /> : signals.length > 0 ? <p role="status" style={{ padding: 12 }}>No rows match the current filters. Reset the EMA view to show all recorded signals.</p> : null}
    </div>
  </div>;
}

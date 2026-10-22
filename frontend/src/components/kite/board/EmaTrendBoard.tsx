import React from 'react';
import { useEmaTrendConfig, useEmaTrendSnapshot, useRunEmaTrendScan, useEmaTrendScan, useCancelEmaTrendScan, useEmaTrendOperation, useUpdateEmaTrend } from '../../../hooks/useEmaTrend';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from './emaTrendAdapter';
import type { BoardSignal } from './boardTypes';
import { useSignalMarketData } from './useSignalMarketData';
import { SignalBoard } from './SignalBoard';
import { BoardFilters } from './BoardFilters';
import { StrategyBoardFooter } from './StrategyBoardFooter';
import { useSignalColumns } from './useSignalColumns';
import { KiteActionButtons } from '../KiteActionButtons';
import { useBoardView } from './useBoardView';
import { EmaTrendTicket } from './EmaTrendTicket';
import { EmaTrendOperations } from './EmaTrendOperations';
import { selectEmaViewport } from './emaTrendViewport';
import { openSettingsSection } from '../config/registry';
import { EMA_TREND_MODE_LABEL, type EmaTrendMode } from '../../../types/emaTrend';
import { EMA_TREND_QUICKSTART } from '../../../config/emaTrendPreset';

const MODES: EmaTrendMode[] = ['swing', 'intraday', 'scalping', 'ultra_scalping'];
const AUTO_SCAN_INTERVAL_MS = 60_000;
const INITIAL_FETCH_ROWS_PER_MODE = 200;
const INITIAL_RENDER_ROWS = 80;
const ROW_INCREMENT = 80;

/** Completed-bar evidence view; selecting profiles never arms a broker order. */
/**
 * EMA Trend checks each stock under several internal profiles. The board shows one row per
 * stock: the one closest to a trade (ready first, then the newest), so a stock is not listed
 * three times. Open campaigns are listed separately and are never merged away.
 */
const RANK: Record<string, number> = { armed: 0, running: 1, weakening: 2, watching: 3, error: 4, ended: 5 };
function oneRowPerStock<T extends { underlying: string; status: string; atMs: number | null; executionEligible?: boolean }>(rows: T[]): T[] {
  const best = new Map<string, T>();
  for (const row of rows) {
    const cur = best.get(row.underlying);
    const score = (r: T) => [r.executionEligible ? 0 : 1, RANK[r.status] ?? 9, -(r.atMs ?? 0)];
    if (!cur) { best.set(row.underlying, row); continue; }
    const a = score(row), b = score(cur);
    if (a[0] < b[0] || (a[0] === b[0] && (a[1] < b[1] || (a[1] === b[1] && a[2] < b[2])))) best.set(row.underlying, row);
  }
  return [...best.values()];
}

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
  const [fetchLimit, setFetchLimit] = React.useState(INITIAL_FETCH_ROWS_PER_MODE);
  const [renderLimit, setRenderLimit] = React.useState(INITIAL_RENDER_ROWS);
  const snapshot = useEmaTrendSnapshot(selectedModes, fetchLimit);
  const scan = useRunEmaTrendScan();
  // Prefer the just-requested job over a stale last-scan id from a snapshot.
  // Otherwise the UI can claim an old scan is complete while a new one runs.
  const job = useEmaTrendScan(scan.data?.id || snapshot.data?.scan?.id);
  const cancel = useCancelEmaTrendScan();
  const operation = useEmaTrendOperation();
  const quickstart = useUpdateEmaTrend();
  const [openId, setOpenId] = React.useState<string | null>(null);
  const lastAutoRequest = React.useRef<{ key: string; at: number } | null>(null);

  const activeModes = config.data?.config.modes || [];
  const hasSymbols = Boolean(config.data &&
    ((config.data.config.scan_stocks_enabled !== false && config.data.config.symbols.length > 0) ||
      (config.data.config.scan_indices?.length || 0) > 0));
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

  const adaptedSignals = React.useMemo(() => historical ? [] : [
    ...emaTrendCampaignsToBoard(snapshot.data?.campaigns || []),
    ...oneRowPerStock(emaTrendSetupsToBoard(snapshot.data?.rows || []).map(row => ({
      ...row,
      executionEligible: row.executionEligible &&
        row.emaTrend?.configRevision === snapshot.data?.revision &&
        snapshot.data?.config.enabled === true &&
        snapshot.data.paused !== true && snapshot.data.capabilities?.paper === true,
    }))),
  ], [snapshot.data, historical]);
  // Live price and today's change for every listed stock (display only).
  const signals = useSignalMarketData(adaptedSignals, historical);
  // v4 starts with an unrestricted view; stale TODAY/ENDED preferences must
  // not make genuine older saved signals look like a broken scanner.
  const commonColumns = useSignalColumns();
  const view = useBoardView(signals, { storageKey: 'ema_trend_profiles_v5', endedByDefault: true,
    defaultHidden: ['engine', 'score', 'leg', 'target', 'exit'] });
  // Keep the UI bounded while retaining all unresolved/open campaigns.
  const visible = React.useMemo(
    () => selectEmaViewport(view.visible, signals, renderLimit),
    [signals, view.visible, renderLimit],
  );
  const hasMoreRendered = view.visible.length > renderLimit;
  const hasOlderServerRows = (snapshot.data?.truncatedModes?.length || 0) > 0 && fetchLimit < 1000;
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
        scan_stocks_enabled: true,
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
    setRenderLimit(INITIAL_RENDER_ROWS);
  };
  const resetProfiles = () => { setSelectedModes([...MODES]); setOpenId(null); setRenderLimit(INITIAL_RENDER_ROWS); };
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
    !hasSymbols ? 'No names picked. Start with a sample scan, or open Settings' :
    selectedEnabled.length === 0 ? 'Turn on a mode in EMA settings to scan it' :
    !scannerEnabled ? 'Scanner off — enable EMA in Settings' :
    lastScan?.status === 'FAILED' ? 'Scan failed — review details' :
    `${signals.length} saved records`;

  return <div className="sb-strategy-layout" style={{ height: '100%', overflow: 'auto' }}>
    <div className="sb-strategy-rows">
      <BoardFilters hideSearch view={{ ...view, ...commonColumns,
        offers: { ...view.offers, best: false } }} columns={commonColumns.columns}>
        <StrategyBoardFooter name="EMA Trend" onReset={resetProfiles}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '8px 12px' }}>
        <button type="button" className="sb-tool" disabled={!canScan}
          onClick={() => scan.mutate(activeModes)}>
          {scanning ? 'Scanning…' : 'Scan now'}
        </button>
        {scanning && <button type="button" className="sb-tool"
          disabled={cancel.isPending || !job.data?.id}
          onClick={() => { if (job.data?.id) cancel.mutate(job.data.id); }}>Stop scan</button>}
        <details className="strategy-scan-modes"><summary>Modes · {selectedModes.length}</summary>
          <div role="group" aria-label="EMA Trend visible modes">
            {MODES.map(mode => <label key={mode}><input type="checkbox" checked={selectedModes.includes(mode)}
              disabled={selectedModes.length === 1 && selectedModes.includes(mode)}
              onChange={() => toggleMode(mode)} />{EMA_TREND_MODE_LABEL[mode]}</label>)}
          </div></details>
        <span role="status" style={{ fontSize: 11, color: 'var(--k-dim)' }}>{status}</span>
        <a href="#" className="sb-tool" onClick={(e) => { e.preventDefault(); openSettingsSection('emaTrend'); }}>Settings</a>
        {signals.some(row => row.emaTrend?.campaignId && row.status !== 'ended') &&
          <button type="button" className="sb-tool" disabled={historical || operation.isPending}
            onClick={() => {
              if (window.confirm('Close all EMA practice trades? Real Kite positions are not affected.')) operation.mutate('flatten');
            }}>Close simulated campaigns</button>}
        <details style={{ fontSize: 11, maxWidth: '100%' }}>
          <summary style={{ cursor: 'pointer' }}>
            Practice only · {safetyBlockers.length} safety checks
          </summary>
          <div style={{ padding: '8px 10px', lineHeight: 1.5, maxWidth: 440 }}>
            <strong>Practice trades only. No real orders.</strong>
            {safetyBlockers.map(reason => <div key={reason}>{reason.replace(/_/g, ' ').toLowerCase()}</div>)}
            <p>EMA 10/21 · DEMA 50 · SuperTrend (10,3). Finished candles only. Not approved for real money.</p>
          </div>
        </details>
        </div>
        </StrategyBoardFooter>
      </BoardFilters>
      {!historical && (snapshot.data?.campaigns?.some(c =>
        c.qty > 0 || Boolean(c.pending || c.pending_close) || c.state === 'RECOVERY_REQUIRED') ||
        (snapshot.data?.supervision && ['STALE', 'DEGRADED'].includes(snapshot.data.supervision.status))) &&
        <EmaTrendOperations
          campaigns={snapshot.data?.campaigns || []}
          supervision={snapshot.data?.supervision}
          unavailable={snapshot.isLoading || snapshot.isError}
          readOnly={historical} />}
      {historical && <p role="status" style={{ padding: 12 }}>Past day, view only. Go back to today for live signals.</p>}
      {operation.isSuccess && operation.variables === 'flatten' &&
        <p role="status" style={{ padding: 12 }}>Close requested for practice trades. Real Kite positions are not affected.</p>}
      {[snapshot.error, config.error, scan.error, cancel.error, operation.error, quickstart.error].filter(Boolean).map((error, index) =>
        <p role="alert" key={index} style={{ padding: 12 }}>{(error as Error).message}</p>)}
      {scanError.length > 0 &&
        <details role="alert" style={{ padding: '6px 12px', fontSize: 11 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--k-amber)' }}>
            Last scan: {scanError.length} name{scanError.length === 1 ? '' : 's'} could not be checked
          </summary>
          <ul style={{ margin: '6px 0', paddingLeft: 18 }}>
            {scanError.slice(0, 20).map((e, i) => <li key={i}>{e.replace(/_/g, ' ').toLowerCase()}</li>)}
          </ul>
        </details>}
      {snapshot.data?.failedModes?.length ? <p role="alert" style={{ padding: 12 }}>
        Could not load saved signals for {snapshot.data.failedModes.map(mode => EMA_TREND_MODE_LABEL[mode]).join(', ')}.
        Other profiles remain visible. Check the EMA API and refresh.
      </p> : null}
      {!historical && snapshot.isLoading && <p style={{ padding: 12 }}>Loading EMA Trend signals…</p>}
      {!historical && !snapshot.isLoading && !hasSymbols && !config.isLoading && !config.isError &&
        <div role="region" aria-label="Set up EMA Trend signals" style={{ padding: 14, display: 'grid', gap: 8,
          border: '1px solid var(--k-border)', borderRadius: 8, margin: 10 }}>
          <strong>No names picked for EMA Trend.</strong>
          <span>Pick names in Settings, or start with 5 NSE stocks in all modes. Scans only; no trades.</span>
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
          {selectedEnabled.length === 0 ? 'No mode is on. Turn one on in EMA Trend settings.' :
            !scannerEnabled ? 'Scanning is off. Turn it on in Settings.' :
            scanning ? 'Scanning your names…' :
            currentScan?.status === 'FAILED' ? 'Last scan failed. See the errors above.' :
            currentScan?.status === 'IDLE' || !currentScan?.id ? 'No scan yet. Press Refresh signals or leave scanning on.' :
            scanResults.length > 0
              ? `Last scan processed ${processedPairs} symbol/profile pairs and ${providerBars} source candles, but no setup records are available for these profiles. Review scanner errors or data gaps.`
              : 'No signals yet. Checked on each finished candle.'}
        </p>}
      {snapshot.data?.truncatedModes?.length ? <p role="status" style={{ padding: 12 }}>
        Showing the newest {fetchLimit} signals. Older ones are kept in the history.
      </p> : null}
      {unavailableSelected.length > 0 && signals.length > 0 &&
        <p role="status" style={{ padding: '4px 12px', fontSize: 11 }}>
          Not enabled for new scans: {unavailableSelected.map(mode => EMA_TREND_MODE_LABEL[mode]).join(', ')}. Any existing history is still shown.
        </p>}
      {visible.length > 0 ? <SignalBoard signals={visible} columns={commonColumns.columns} hidden={commonColumns.hidden}
        onReorderColumn={commonColumns.onReorderColumn} rowScroll={commonColumns.rowScroll}
        isHistoricalSim={historical} strategyDetailsInExpansion openId={openId}
        onToggle={id => setOpenId(previous => previous === id ? null : id)}
        onOpenDetail={onOpenDetail}
        renderTrade={signal => signal.emaTrend?.signalId ? <KiteActionButtons className="sb-row-trade"
          buyAriaLabel={`Practice buy ${signal.instrument.symbol}`}
          buyDisabled={historical || signal.status !== 'armed' || !signal.executionEligible}
          disabledHint={historical ? 'Past signals are view only.' : 'Not ready to enter.'}
          onBuy={() => setOpenId(signal.id)} /> : null}
        renderChart={signal => onOpenChart && <button type="button" className="sb-tool"
          disabled={historical || signal.historical} aria-label={`Chart ${signal.instrument.symbol}`}
          onClick={event => { event.stopPropagation(); onOpenChart(signal.instrument.quoteKey!, signal.emaTrend!.mode, signal); }}>↗</button>}
        renderDetail={signal => <EmaTrendTicket signal={signal} readOnly={historical} />}
      /> : signals.length > 0 ? <p role="status" style={{ padding: 12 }}>Nothing matches these filters. Reset the view to see all.</p> : null}
      {signals.length > 0 && (hasMoreRendered || hasOlderServerRows) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, padding: '8px 12px' }}>
          <span role="status" style={{ fontSize: 11, opacity: 0.8 }}>
            {visible.length} rendered · {view.visible.length} matching loaded records
          </span>
          {hasMoreRendered && <button className="sb-tool" type="button"
            onClick={() => setRenderLimit(value => value + ROW_INCREMENT)}>Show 80 more</button>}
          {hasOlderServerRows && <button className="sb-tool" type="button" disabled={snapshot.isFetching}
            onClick={() => setFetchLimit(value => Math.min(1000, value + 200))}>
            {snapshot.isFetching ? 'Loading…' : 'Load older records'}
          </button>}
        </div>
      )}
    </div>
  </div>;
}

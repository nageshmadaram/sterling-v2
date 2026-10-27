import { useSimNowMs } from '../../../hooks/useReplayStore';
/**
 * Snapback, on the shared board.
 *
 * Two things this board does that no other one needs to.
 *
 * **It shows the measured scorecard, not a hard-coded verdict.** The pass count,
 * failed checks and promotion state come from the backend validation artifact.
 * Broker execution capability is reported separately and can remain blocked
 * even when a signal itself is armed.
 *
 * **It says when the rows can next change.** Every rule here is stated on a
 * daily CLOSE, so a board watched through the session will not move. Without
 * saying so, a quiet board reads as a broken one — which is the bug this repo
 * keeps re-fixing under different names.
 */
import React from 'react';
import { useSavedSelection } from '../../../hooks/useSavedSelection';
import { KiteActionButtons } from '../KiteActionButtons';
import {
  useStopSnapbackScan, useExecuteSnapbackSignal, useRunSnapbackScan, useSnapbackHistory, useSnapbackSnapshot,
} from '../../../hooks/useSnapback';
import type { SnapbackValidation } from '../../../hooks/useSnapback';
import { snapbackRowsToBoard } from './snapbackAdapter';
import { BOARD_COLUMNS, SignalBoard } from './SignalBoard';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardRowActions } from './useBoardRowActions';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';
import { openSettingsSection } from '../config/registry';
import { useSignalColumns } from './useSignalColumns';
import { StrategyBoardFooter } from './StrategyBoardFooter';
import { useSignalMarketData } from './useSignalMarketData';
import { useTodaySignalPriority } from './TodaySignalPriority';

const note: React.CSSProperties = {
  padding: '10px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6,
};

/** Plain-English names for the gate's checks. The keys are the backend's. */
const CHECK_LABEL: Record<string, string> = {
  enough_trades: 'Enough trades',
  enough_days: 'Enough distinct entry days',
  profitable: 'Profitable out of sample',
  beats_random_timing: 'Beats random entry timing',
  deflated_sharpe: 'Survives the variant count',
  priced_edge: 'Edge bigger than the option costs',
  consistent_across_years: 'Positive every calendar year',
  survivable_drawdown: 'Survivable drawdown',
  mean_proven: 'Size of the edge proven',
};

const CHECK_HINT: Record<string, string> = {
  beats_random_timing: 'The research harness reports a random-entry comparison. Its dataset and exposure-matching assumptions must be reviewed separately.',
  deflated_sharpe: 'Adjusts the reported Sharpe for the number of strategy variants tried.',
  priced_edge: 'A model sensitivity to assumed option costs; actual premiums, spreads and fills can differ.',
  mean_proven: 'Checks the confidence interval reported by the research harness. An interval containing zero does not establish a positive mean return.',
};

function Evidence({ v }: { v: SnapbackValidation }) {
  const entries = Object.entries(v.checks ?? {});
  if (!entries.length) return null;
  const failed = entries.filter(([, ok]) => !ok);
  return (
    <div style={{
      padding: '8px 12px', borderBottom: `1px solid ${k.border}`, fontSize: 11,
      lineHeight: 1.6,
      background: 'color-mix(in srgb, var(--k-amber) 6%, transparent)',
    }}>
      <div style={{ color: k.text }}>
        <strong style={{ color: v.promoted ? k.green : k.amber }}>
          Research model · {v.promoted ? 'HARNESS GATE PASSED' : `PASSES ${v.passed} OF ${v.total_checks} CHECKS`}
        </strong>{' '}
        Modelled out of sample it took {v.oos_trades} trades on {v.oos_entry_days} distinct
        entry days, returning {v.oos_mean_day_return_pct >= 0 ? '+' : ''}
        {v.oos_mean_day_return_pct.toFixed(2)}% per entry day at a Sharpe of{' '}
        {v.sharpe.toFixed(2)}, worst drawdown {v.max_drawdown_pct.toFixed(1)}% at{' '}
        {v.allocation_pct}% of capital per position.
      </div>
      <div style={{ marginTop: 4, color: k.dim }}>Research results are not live execution certification. Current broker, protection and risk readiness are checked separately.</div>
      <div style={{ marginTop: 4 }}>
        {entries.map(([key, ok]) => (
          <span
            key={key}
            title={CHECK_HINT[key]}
            style={{
              display: 'inline-block', marginRight: 10,
              color: ok ? k.green : k.amber,
              cursor: CHECK_HINT[key] ? 'help' : undefined,
            }}
          >
            {ok ? '✓' : '✗'} {CHECK_LABEL[key] ?? key}
          </span>
        ))}
      </div>
      {failed.length > 0 && (
        <div style={{ marginTop: 4, color: k.dim }}>
          {/* The reasons verbatim from the harness. A summary of a summary is
              where a caveat quietly becomes a slogan. */}
          {v.reasons.map((r, i) => <div key={i}>· {r}</div>)}
        </div>
      )}
      <div style={{ marginTop: 4, color: k.dim }}>
        Measured {v.measured_at} on {v.span} across {v.universe.length} instruments,
        at {v.slippage_pct ?? '—'}% slippage per leg. Break-even vol multiple{' '}
        <b style={{ color: k.text }}>{v.breakeven_vrp ?? '—'}x</b> against the model’s assumed
        1.15–1.30x band. Entry-timing p{' '}
        <b style={{ color: k.text }}>{v.permutation_p ?? '—'}</b>.
      </div>
    </div>
  );
}

export function SnapbackBoard({ nowMs = Date.now(), onOpenDetail, onOpenChart }: {
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  const replayNow = useSimNowMs();
  const replay = replayNow !== null;
  nowMs = replayNow ?? nowMs;
  const rowActions = useBoardRowActions({ onOpenChart });
  const commonColumns = useSignalColumns();
  const priority = useTodaySignalPriority();
  const snapshot = useSnapbackSnapshot();
  const scan = useRunSnapbackScan();
  const stopScan = useStopSnapbackScan();
  const execute = useExecuteSnapbackSignal();
  const data = snapshot.data;
  const mode = data?.trading_mode ?? data?.config?.trading_mode ?? 'swing';
  const [savedModes, setSavedModes] = useSavedSelection<string>('snapback.scanModes', '');
  let selectedModes: string[] = [];
  try { selectedModes = JSON.parse(savedModes || '[]').filter((m:string)=>['swing','scalp','intraday','ultra_scalping','overnight'].includes(m)); } catch { /* use configured mode */ }
  if (!selectedModes.length) selectedModes = [mode];
  const scanBusy = scan.isPending || data?.scanning === true;
  const shortHorizon = !selectedModes.includes('swing');
  const researchOnly = false;
  // Replayed from stored bars. The live scan only looks at the last few closed
  // sessions and this rule fires on ONE, so without these the board is blank
  // almost always — and blank reads as broken.
  const history = useSnapbackHistory(30, priority.olderReady && !shortHorizon && !replay);

  const adapted = React.useMemo(() => {
    const live = (data?.rows ?? []).filter((r) =>
      selectedModes.includes(r.trading_mode ?? 'swing') &&
      (priority.olderReady || r.timestamp_ms && new Date(r.timestamp_ms)
        .toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }) === priority.day));
    const seen = new Set(live.map((r) => r.signal_id));
    const past = (shortHorizon || replay || !priority.olderReady ? [] : history.data?.signals ?? []).map((r) =>
      seen.has(r.signal_id) ? { ...r, signal_id: `${r.signal_id}:history` } : r);
    return snapbackRowsToBoard([...live, ...past], replay ? [] : data?.positions);
  }, [data?.rows, data?.positions, history.data?.signals, mode, savedModes, shortHorizon, replay, priority.olderReady, priority.day]);
  const signals = useSignalMarketData(adapted, replay);
  const view = useBoardView(signals, {
    endedByDefault: true, storageKey: 'snapback', nowMs,
  });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading Snapback…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const armed = signals.filter((s) => s.status === 'armed');
  const validation = data?.strategy?.validation ?? null;
  const enabled = data?.config?.enabled === true;
  const manualCapability = data?.capabilities?.manual_execution;
  const autoCapability = data?.capabilities?.auto_execution;
  const manualExecutable = manualCapability?.single_leg === true && !(autoCapability?.session?.length);
  const autoExecutable = autoCapability?.available === true;
  const explainReason = (reason?: string | null) => reason?.replace(
    'external_exposure_unreadable', 'Broker inventory could not be verified',
  ) ?? null;
  const manualReason = explainReason(manualCapability?.reason);
  const autoReason = explainReason(autoCapability?.reason);
  const sharedBlocker = !autoExecutable && !manualExecutable && autoReason && autoReason === manualReason;
  const needsLogin = /not logged in to Kite|no_broker_session|session.*expired|access.token/i.test(`${autoReason ?? ''} ${manualReason ?? ''}`);
  // Older running backends report the configuration switch as execution status.
  const warnings = (data?.warnings ?? []).map(w => w.replace(
    'Automatic execution is on.', 'ALGO is enabled in settings; broker readiness determines whether entries can execute.',
  ));

  return (
    <div className="sb-strategy-layout" style={{fontFamily:k.fontFamily}}>
      <div className="sb-strategy-rows">
      <BoardFilters hideSearch showModes view={{ ...view, ...commonColumns }} columns={commonColumns.columns}>
        <StrategyBoardFooter name="Snapback" onReset={()=>{setSavedModes('');setOpenId(null);}}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => scan.mutate(selectedModes)}
          disabled={scanBusy || !enabled || replay}
          title={enabled
            ? `Scan the universe using completed ${shortHorizon ? 'minute' : 'daily'} candles`
            : 'Snapback is switched off'}
          style={{
            background: 'transparent', border: `1px solid ${k.border}`,
            color: k.text, borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: scan.isPending ? 'progress' : enabled ? 'pointer' : 'default',
            opacity: enabled ? 1 : 0.55,
          }}
        >
          {scanBusy ? 'Scanning…' : 'Scan now'}
        </button>
        <details className="snapback-scan-modes"><summary>Modes · {selectedModes.length}</summary><div role="group" aria-label="Snapback scan modes">
          {[['ultra_scalping','Ultra scalping'],['scalp','Scalp'],['intraday','Intraday'],['overnight','Overnight'],['swing','Swing · daily']].map(([value,label])=><label key={value}><input type="checkbox" checked={selectedModes.includes(value)} disabled={replay || scanBusy || (selectedModes.length===1 && selectedModes.includes(value))} onChange={()=>setSavedModes(JSON.stringify(selectedModes.includes(value)?selectedModes.filter(m=>m!==value):[...selectedModes,value]))}/>{label}</label>)}
        </div></details>
        {scanBusy && <button type="button" disabled={stopScan.isPending || (!data?.scan_cancellable && !scan.isPending)} onClick={()=>stopScan.mutate()}>{stopScan.isPending?'Stopping…':'Stop scan'}</button>}
        <span style={{ fontSize: 11, color: k.dim }}>
          {data?.scanned ?? 0}{scanBusy && data?.scan_total ? ` / ${data.scan_total}` : ''} instruments
          {scanBusy ? ' · live scan in progress; preview rows are view-only' : ''} · {selectedModes.length>1 ? selectedModes.join(' + ') : shortHorizon
            ? `${data?.config?.scalp_timeframe_minutes ?? 1}-minute bars · ${mode}` : 'daily bars'}
          {replay
            ? <> · <strong style={{ color: k.blue }}>REPLAY · VIEW ONLY</strong></>
            : researchOnly
            ? <> · <strong style={{ color: k.amber }}>TEST MODE · NO REAL ORDERS</strong></>
            : (autoCapability?.session?.length ?? 0) > 0
              ? <> · <strong style={{ color: k.amber }}>MARKET CLOSED</strong><span style={{ color: k.dim }}> · entries require an open session and a fresh plan</span></>
            : autoExecutable
              ? <> · <strong style={{ color: k.green }}>AUTO TRADING READY</strong></>
              : manualExecutable
                ? <> · <strong style={{ color: k.amber }}>BUY BY HAND ONLY</strong></>
                : <> · <strong style={{ color: k.red }}>TRADING BLOCKED · open a row to see why</strong></>}
          {enabled ? '' : ' · strategy off'}
        </span>
        {armed.length > 0 && (
          <span style={{
            fontSize: 11,
            color: manualExecutable && !researchOnly ? k.green : k.amber,
            marginLeft: 'auto',
          }}>
            {manualExecutable && !researchOnly
              ? `${armed.length} armed — open a row to buy it`
              : `${armed.length} signal${armed.length === 1 ? '' : 's'} armed — execution unavailable`}
          </span>
        )}
      </div>

      <button className="sb-help-link" type="button" onClick={() => window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: 'help' }))}>Snapback help ↗</button>
      {data?.auto_execution_blocker && data.auto_execution_blocker !== autoReason && (
        <p style={{ ...note, color: k.amber, borderBottom: `1px solid ${k.border}` }}>
          Strategy gate: {data.auto_execution_blocker}
        </p>
      )}

      {!researchOnly && sharedBlocker && (
        <p role="status" style={{ ...note, color: k.amber, borderBottom: `1px solid ${k.border}` }}>
          New entries are unavailable. {autoReason}
          {needsLogin && <> <button type="button" onClick={() => openSettingsSection('account')}>Reconnect Kite ↗</button></>}
        </p>
      )}

      {!researchOnly && !sharedBlocker && !autoExecutable && autoReason && (
        <p style={{ ...note, color: k.red, borderBottom: `1px solid ${k.border}` }}>
          Automatic execution is blocked. {autoReason}
        </p>
      )}

      {!researchOnly && !sharedBlocker && !manualExecutable && manualReason && (
        <p style={{ ...note, color: k.red, borderBottom: `1px solid ${k.border}` }}>
          Manual execution is blocked. {manualReason}
        </p>
      )}

      {warnings.length > 0 && <details className="strategy-help"><summary>ⓘ Strategy risk notes ({warnings.length})</summary>{warnings.map((w) => (
        <p key={w} style={{ ...note, color: k.amber }}>{w}</p>
      ))}</details>}

      {data?.last_error && signals.length > 0 && (
        <p style={{ ...note, color: k.amber, borderBottom: `1px solid ${k.border}` }}>
          Last scan failed: {data.last_error}
        </p>
      )}

      {execute.error && <p role="alert" style={{ ...note, color: k.red }}>
        Entry failed: {(execute.error as Error).message}
      </p>}
      {data?.positions_error && <p role="alert" style={{ ...note, color: k.red }}>{data.positions_error}</p>}
      {stopScan.error && <p role="alert" style={{...note,color:k.red}}>Stop failed: {(stopScan.error as Error).message}</p>}
      {scan.error && <p role="alert" style={{ ...note, color: k.red }}>
        Scan failed: {(scan.error as Error).message}
      </p>}

      {(data?.failures?.length ?? 0) > 0 && (
        <div style={{ ...note, borderTop: `1px solid ${k.border}` }}>
          {/* Never silently dropped. A symbol that could not be scanned is the
              difference between "no signal" and "no data". */}
          {data!.failures.length} instrument
          {data!.failures.length === 1 ? '' : 's'} could not be scanned:{' '}
          {data!.failures.slice(0, 4).join('; ')}
        </div>
      )}
      </StrategyBoardFooter>
      </BoardFilters>
      {signals.length === 0 ? (
        <p style={note}>
          {scanBusy
            ? `Scanning market candles and contracts (${data?.scanned ?? 0} / ${data?.scan_total ?? '?'} instruments). Signals appear as individual symbols finish; entries require complete validation.`
            : data?.last_error
              ? `Last scan failed: ${data.last_error}`
              : shortHorizon
                ? 'No eligible reversal on the latest completed bars.'
                : 'No signals in the recent completed sessions.'}
        </p>
      ) : (
        <>

          <SignalBoard
            isHistoricalSim={replay}
        strategyDetailsInExpansion
            renderTrade={(sig) => {
              const eligible = sig.status === 'armed' && manualExecutable && !researchOnly && !replay && !sig.historical && sig.executionEligible !== false;
              return <KiteActionButtons className="sb-row-trade"
                buyAriaLabel={`Execute Snapback ${sig.instrument.symbol}`}
                buyLabel={execute.isPending && execute.variables === sig.id ? '…' : undefined}
                buyDisabled={!eligible || execute.isPending}
                disabledHint={replay || sig.historical ? 'Historical signals are read-only.' : sig.reason || manualReason || 'This signal is not eligible for entry'}
                onBuy={() => { if (eligible) execute.mutate(sig.id); }} />;
            }}
            renderChart={rowActions.renderChart}
            signals={view.visible}
            onOpenDetail={onOpenDetail}
            columns={commonColumns.columns}
            hidden={commonColumns.hidden}
            onReorderColumn={commonColumns.onReorderColumn}
            rowScroll={commonColumns.rowScroll}
            openId={openId}
            onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
            renderDetail={(sig) => {
              const canExecute = sig.status === 'armed' && manualExecutable && !researchOnly && !replay && !sig.historical && sig.executionEligible !== false;
              const executing = execute.isPending && execute.variables === sig.id;
              return (
                <div>
                  {!shortHorizon && validation && <Evidence v={validation} />}
                  {/* Snapback exposure must go through its plan/arm/dispatcher path.
                      BoardTicket remains read-only for quote/depth/calculator context. */}
                  <BoardTicket signal={sig} tag="SNAPBACK" allowTrade={false} executionAction={
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                    <button
                      type="button"
                      disabled={!canExecute || execute.isPending}
                      onClick={() => { if (canExecute) execute.mutate(sig.id); }}
                      title={canExecute
                        ? 'Publish a fresh server-owned Snapback plan and arm that exact revision'
                        : researchOnly
                          ? 'Execution requires current server approval for this signal and broker readiness'
                          : (sig.executionEligible === false
                              ? sig.reason || 'This row is not currently eligible for Snapback execution'
                              : (manualReason || 'Snapback execution is blocked'))}
                      style={{
                        background: canExecute ? k.green : 'transparent',
                        border: `1px solid ${canExecute ? k.green : k.border}`,
                        color: canExecute ? '#fff' : k.dim,
                        borderRadius: 6, padding: '4px 10px', fontSize: 11,
                        cursor: canExecute && !execute.isPending ? 'pointer' : 'default',
                        opacity: canExecute ? 1 : 0.55,
                      }}
                    >
                      {executing ? 'Arming…' : 'Execute Snapback'}
                    </button>
                    {!canExecute && <span role="status" style={{ fontSize: 11, color: k.dim }}>
                      {replay || sig.historical ? 'Historical signals are read-only.' : sig.reason || manualReason || 'This signal is not eligible for entry.'}
                    </span>}
                    <button
                      type="button"
                      onClick={() => onOpenDetail?.(sig)}
                      style={{
                        background: 'transparent', border: `1px solid ${k.border}`,
                        color: k.dim, borderRadius: 6, padding: '4px 10px',
                        fontSize: 11, cursor: 'pointer',
                      }}
                    >
                      Open detail
                    </button>
                  </div>} />
                </div>
              );
            }}
            nowMs={nowMs}
          />
        </>
      )}

      </div>

    </div>
  );
}

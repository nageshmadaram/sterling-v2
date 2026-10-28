import { StrategyIntro } from './board/StrategyIntro';
import {useSavedSelection} from '../../hooks/useSavedSelection';
const PriceRulesBoard = lazy(() => import('./board/PriceRulesBoard').then(m => ({ default: m.PriceRulesBoard })));
import { openSettingsSection } from './config/registry';
import React, { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useEffectiveNowMs } from '../../hooks/useReplayStore';
import { useTodaySignalPriority } from './board/TodaySignalPriority';
const SterlingKiteEngineWithExpiry = lazy(() => import('./SterlingKiteEngineWithExpiry').then(m => ({ default: m.SterlingKiteEngineWithExpiry })));
const GammaMoveBoard = lazy(() => import('./board/GammaMoveBoard').then(m => ({ default: m.GammaMoveBoard })));
const EmaTrendBoard = lazy(() => import('./board/EmaTrendBoard').then(m => ({ default: m.EmaTrendBoard })));
import { useEmaTrendSnapshot } from '../../hooks/useEmaTrend';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from './board/emaTrendAdapter';
const SnapbackBoard = lazy(() => import('./board/SnapbackBoard').then(m => ({ default: m.SnapbackBoard })));
import { EngineTabs, type EngineTabState } from './board/EngineToolbar';
import { gammaMoveToBoard } from './board/gammaMoveAdapter';
import { supertrendToBoard } from './board/supertrendAdapter';
import { ACTIONABLE, type BoardSignal, type EngineId } from './board/boardTypes';
import { useReleaseEngineEnabled } from '../../hooks/useEngineToggles';
import { usePriceRulesSwitch } from '../../hooks/usePriceRulesSwitch';
import { useIsSimulation } from '../../store/useViewStore';
import { useEngineConfig, useEngineSignals } from '../../hooks/useSterlingKiteEngine';
import { useSnapbackSnapshot } from '../../hooks/useSnapback';
import { snapbackRowsToBoard } from './board/snapbackAdapter';
import { useGammaMoveSnapshot } from '../../hooks/useGammaMove';
import { k, Icons } from '../../styles/kiteUI';
import { orderEngines, useKiteSettings } from '../../store/useKiteSettings';
import { PaneHeaderActions } from './PaneHeaderActions';
import { ToolbarButton } from './board/EngineToolbar';
import { ScanCountdownRing } from './board/ScanCountdownRing';
const SignalTableSettingsPanel = lazy(() => import('./SterlingKiteEnginePane').then(m => ({ default: m.SignalTableSettingsPanel })));
import { SCANNABLE_ENGINE_LABEL, useScanStrategies, type ScannableEngine } from '../../hooks/useScanStrategies';
import { useScanActivity } from '../../store/useScanActivity';
import { useCancelScan } from '../../hooks/useSterlingKiteEngine';

/**
 * The engine workspace: pick an engine, see its board.
 *
 * The picker used to be three flat words. Choosing between them meant opening
 * each one to find out whether it was running and whether it had anything —
 * which is backwards, because the point of a picker is to make that choice
 * without paying for it.
 *
 * Each tab now carries live state: a dot for running / running-but-quiet / off,
 * and a count of what is live. Every tab therefore has to know its engine's
 * state whether or not it is the visible one, which is why the counts are read
 * here rather than inside each board. All three already poll on their own
 * schedule, so this shares their cached data rather than adding requests.
 */
interface Props {
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  onOpenChart?: (symbol: string, tab: 'chart', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: any) => void;
  /** Opens a board signal as a full detail page in the centre column. */
  onOpenBoardDetail?: (signal: BoardSignal) => void;
}

/** Which engine each nav destination should land on. */
const NAV_TARGET: Record<string, EngineId> = {
  gammaMove: 'gamma_move',
  snapback: 'snapback',
  emaTrend: 'ema_trend',
};

export function AdaptiveEdgeRightSidebar({ onSelectSignal, onOpenChart, onOpenBoardDetail }: Props) {
  // The operator's choice, not a hardcoded 'supertrend'. Corrected below once
  // the tab list is known: an engine that is switched off renders no tab, and
  // selecting one would land on an empty pane that reads as a fault.
  const preferredEngine = useKiteSettings((st) => st.defaultSignalEngine);
  const engineOrder = useKiteSettings((st) => st.engineOrder);
  const [engine, setEngine] = useSavedSelection<EngineId | 'price_rules'>('workspace.engine', (preferredEngine as EngineId) || 'supertrend');
  // Direct browser notifications open the originating strategy rather than
  // the last visited tab. Treat the route as read-only UI state, never admission.
  const notificationEngineHandled = React.useRef(false);
  React.useEffect(() => {
    if (notificationEngineHandled.current) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('view') !== 'live' || params.get('notification') !== 'signals') return;
    const requested = params.get('strategy');
    if (!['supertrend', 'snapback', 'gamma_move', 'ema_trend'].includes(requested ?? '')) return;
    notificationEngineHandled.current = true;
    setEngine(requested as EngineId);
  }, [setEngine]);
  const engineOn = useReleaseEngineEnabled();
  const openChartFor = React.useCallback(
    (quoteKey: string) => onOpenChart?.(quoteKey, 'chart'),
    [onOpenChart],
  );

  const [settingsOpen, setSettingsOpen] = useState(false);
  const rescanStrategies = useKiteSettings((st) => st.rescanStrategies);
  const { scanAll, cancelAll, isPending: scanPending } = useScanStrategies();
  const activeManualScan = useScanActivity((st) => st.current);
  const cancelScan = useCancelScan();
  const nowMs = useEffectiveNowMs();
  const priority = useTodaySignalPriority();

  // Reuse the cross-strategy today's cache. Unrestricted records only after
  // every primary current-day source has responded; do not prefetch history
  // just to display tab counts while today's signals are still loading.
  const engineSignals = useEngineSignals(!priority.olderReady);
  const gmSnapshot = useGammaMoveSnapshot();
  const sbSnapshot = useSnapbackSnapshot();
  const emaSnapshot = useEmaTrendSnapshot(['swing'], 200, priority.olderReady ? undefined : priority.day);
  const stCfg = useEngineConfig();


  const sig = engineSignals.data;
  const scanning = sig?.scanning ?? false;

  /**
   * Rescan order: the engine you are looking at first.
   *
   * They share one historical-data budget, so they run one at a time — which
   * makes the order the difference between the board in front of you refreshing
   * now or in twenty seconds.
   */
  const scanOrder = useMemo<ScannableEngine[]>(() => {
    const all: ScannableEngine[] = ['supertrend', 'gamma_move', 'snapback', 'ema_trend'];
    const first = all.filter((e) => e === engine);
    return [...first, ...all.filter((e) => e !== engine)];
  }, [engine]);

  /**
   * The engines a press will ACTUALLY scan.
   *
   * Switched-off engines are skipped. Scanning one would be work the operator has
   * explicitly declined, and it would make the button's own tooltip a lie — it
   * names what it will run, including "Navigator is off".
   *
   * Derived once and used by both the title and the press, because those two
   * disagreeing is exactly the bug this replaces: the old button said "Re-scan
   * both engines" whether it scanned one or two.
   */
  const enabledToScan = useMemo<ScannableEngine[]>(() => scanOrder.filter((e) => {
    // The operator's own choice of what this button covers. Absent means
    // included, so the map only holds exclusions and a new engine is in from the
    // day it appears.
    if (rescanStrategies[e] === false) return false;
    // The running switch is authoritative. UNKNOWN config is fail-closed in
    // useReleaseEngineEnabled(), so a broken/loading config query cannot silently turn
    // into broker work.
    return engineOn[e] === true;
  }), [scanOrder, rescanStrategies, engineOn]);

  /**
   * Name what the press will actually run, in the order it will run it.
   *
   * A button that scans five engines one at a time, skipping the ones that are
   * switched off, must say so — otherwise a press that scanned three looks
   * identical to a press that scanned five. This moved up from SuperTrend's pane
   * with the button; the ORDER it names changed from lens-first to
   * ACTIVE-TAB-first, because the button now belongs to the whole dock and the
   * board in front of you is the one you want refreshed first.
   *
   * ATM Premium Imbalance stays unlisted: it has no scan, it arms one resolved
   * pair. Naming it would promise something the platform cannot do.
   */
  const scanTitle = (() => {
    if (scanning) return `Scanning ${sig?.scanning_label || '…'}`;
    const names = enabledToScan.map((e) => SCANNABLE_ENGINE_LABEL[e]);
    return names.length
      ? `Re-scan ${names.join(', ')}`
      : 'All release strategies are switched off';
  })();

  const tabs: EngineTabState[] = useMemo(() => {
    const st = supertrendToBoard(engineSignals.data?.rows ?? [], { mode: stCfg.data?.horizon_mode });
    const gm = gammaMoveToBoard(gmSnapshot.data);
    const sb = snapbackRowsToBoard(sbSnapshot.data?.rows ?? []);
    const ema = [...emaTrendSetupsToBoard(emaSnapshot.data?.rows || []), ...emaTrendCampaignsToBoard(emaSnapshot.data?.campaigns || [])];
    const live = (list: typeof st) => list.filter((s) => ACTIONABLE.includes(s.status)).length;
    const all: EngineTabState[] = [
      { id: 'supertrend', running: engineOn.supertrend, live: live(st), loadedRecords: st.length },
      { id: 'gamma_move', running: engineOn.gamma_move, live: live(gm), loadedRecords: gm.length },
      { id: 'ema_trend', running: engineOn.ema_trend, live: live(ema), loadedRecords: ema.length },
      { id: 'snapback', running: engineOn.snapback, live: live(sb), loadedRecords: sb.length },
    ];
    // A disabled scanner keeps its board available for history and controls.
    return orderEngines(all, engineOrder);
  }, [engineSignals.data, gmSnapshot.data, sbSnapshot.data, emaSnapshot.data, engineOn, engineOrder, stCfg.data?.horizon_mode]);

  /**
   * Land on the operator's chosen board, and stop as soon as they pick one.
   *
   * Two things make this an effect rather than just the `useState` initialiser.
   * The preference is PERSISTED, so on the first render zustand has not
   * rehydrated it yet and the initialiser reads the fallback — which is how the
   * board kept opening on SuperTrend with 'snapback' sitting in localStorage.
   * And an engine that is switched off renders no tab, so a preference naming
   * one has to fall through to the first tab that exists; selecting a tab that
   * is not rendered leaves the pane blank, which reads as a fault.
   *
   * `touched` is what keeps this from fighting the user: once they click a tab,
   * the preference has had its say for this session.
   */
  // Price Rules has no scanner tab of its own; it follows its Trading Config switch.
  const priceRulesOn = usePriceRulesSwitch(useIsSimulation()).enabled;
  const touched = React.useRef(Boolean(sessionStorage.getItem('sterling.page.workspace.engine')));
  useEffect(() => {
    if (!tabs.length) return;
    const present = (id: string) => (id === 'price_rules' && priceRulesOn) || tabs.some((t) => t.id === id);
    if (!touched.current && preferredEngine && present(preferredEngine)
        && engine !== preferredEngine) {
      setEngine(preferredEngine as EngineId);
      return;
    }
    if (!present(engine)) setEngine(tabs[0].id);
  }, [tabs, engine, preferredEngine, priceRulesOn]);

  const selectEngine = React.useCallback((id: EngineId) => {
    touched.current = true;
    setEngine(id);
  }, []);

  useEffect(() => {
    if (!tabs.length) return;
    if (!(engine === 'price_rules' && priceRulesOn) && !tabs.some((tab) => tab.id === engine)) setEngine(tabs[0].id);
  }, [tabs, engine, priceRulesOn]);

  useEffect(() => {
    const onNav = (event: Event) => {
      const target = NAV_TARGET[(event as CustomEvent<string>).detail];
      if (target) setEngine(target);
    };
    window.addEventListener('kite-nav-click', onNav);
    return () => window.removeEventListener('kite-nav-click', onNav);
  }, []);

  useEffect(() => {
    const onDefaultChanged = (e: Event) => {
      const newDefault = (e as CustomEvent<string>).detail;
      if (newDefault && tabs.some((t) => t.id === newDefault)) {
        if (useKiteSettings.getState().defaultSignalEngine !== newDefault) {
          useKiteSettings.setState({ defaultSignalEngine: newDefault });
        }
        touched.current = false;
        setEngine(newDefault as EngineId);
      }
    };
    window.addEventListener('kite-default-engine-changed', onDefaultChanged);
    return () => window.removeEventListener('kite-default-engine-changed', onDefaultChanged);
  }, [tabs]);

  return (
    <div className="signals-workspace" style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', background: k.bg }}>
      <div className="signal-engine-tabs" style={{ display: 'flex', flexShrink: 0, borderBottom: `1px solid ${k.border}`, background: k.bg }}>
        <EngineTabs tabs={tabs} active={engine as EngineId} onSelect={selectEngine} trailingTab={priceRulesOn ? (
        <button type="button" role="tab" aria-selected={engine === 'price_rules'} title="Price Rules signals — published and armed entry plans"
          aria-label="Open Price Rules strategy" onClick={() => { touched.current = true; setEngine('price_rules'); }}
          className="sb-tab" style={{ flexShrink: 0, padding: '7px 8px', border: 0, borderLeft: `1px solid ${k.border}`, borderBottom: engine === 'price_rules' ? '2px solid var(--k-brand)' : '2px solid transparent',
            background: 'transparent', color: k.text, fontSize: 9.5, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer' }}>
          PRICE RULES
        </button>) : undefined} />

        <button className="strategy-scroll-next" aria-label="Show next strategy" onClick={e=>e.currentTarget.parentElement?.querySelector('[role="tablist"]')?.scrollBy({left:200,behavior:'smooth'})}>›</button>
        <PaneHeaderActions pane="signals">
          {(scanning || scanPending) ? (
            <ToolbarButton
              title={
                activeManualScan === 'supertrend' || (!activeManualScan && scanning)
                  ? 'Stop current scan'
                  : 'Stop after current strategy'
              }
              onClick={() => {
                // Stop the release sweep. SuperTrend exposes a server-side abort;
                // Gamma-Unwind and Snapback stop after the current strategy step.
                cancelAll();
                if (activeManualScan === 'supertrend' || (!activeManualScan && scanning)) {
                  cancelScan.mutate();
                }
              }}
              disabled={
                (activeManualScan === 'supertrend' || (!activeManualScan && scanning))
                && cancelScan.isPending
              }
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
            </ToolbarButton>
          ) : (
            <ToolbarButton title={scanTitle} disabled={scanPending} onClick={() => { void scanAll(enabledToScan); }}>
              <ScanCountdownRing generatedMs={sig?.generated_ms ?? 0}
                nextScanMs={sig?.next_scan_ms ?? 0}
                autoScan={!!sig?.auto_scan && sig?.market_open !== false}
                scanning={scanPending} />
            </ToolbarButton>
          )}
          <span data-signal-table-settings style={{ display: 'inline-flex' }}>
            <ToolbarButton title="Board settings" active={settingsOpen} onClick={() => setSettingsOpen((v) => !v)}>
              <Icons.Settings />
            </ToolbarButton>
          </span>
        </PaneHeaderActions>
      </div>
      <StrategyIntro key={engine} engine={engine} st={stCfg.data} sb={sbSnapshot.data?.config} />

      {settingsOpen && (
        <div style={{ flexShrink: 0, overflow: 'hidden' }}>
          <Suspense fallback={<p role="status">Loading board settings…</p>}><SignalTableSettingsPanel /></Suspense>
        </div>
      )}

      <div className="signal-engine-viewport" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Suspense fallback={<p role="status">Loading strategy signals…</p>}>
        {engine === 'supertrend' && (
          <SterlingKiteEngineWithExpiry onSelectSignal={onSelectSignal} onOpenChart={onOpenChart} />
        )}
        {engine === 'gamma_move' && (
          <GammaMoveBoard onOpenChart={openChartFor} nowMs={nowMs} onOpenDetail={onOpenBoardDetail} />
        )}
        {engine === 'price_rules' && priceRulesOn && <PriceRulesBoard />}
        {engine === 'ema_trend' && <EmaTrendBoard onOpenDetail={onOpenBoardDetail} onOpenChart={(key, mode, signal) => onOpenChart?.(key, 'chart', undefined, { strategy: 'ema_trend', emaTrendMode: mode, emaTrendSignalId: signal.emaTrend?.signalId, emaTrendCampaignId: signal.emaTrend?.campaignId, emaTrendConfigRevision: signal.emaTrend?.configRevision, timestamp_ms: signal.atMs || 0, direction: 'long', regime: 'BULL' })} />}
        {engine === 'snapback' && (
          <SnapbackBoard onOpenChart={openChartFor} onOpenDetail={onOpenBoardDetail} />
        )}
        </Suspense>
      </div>
    </div>
  );
}

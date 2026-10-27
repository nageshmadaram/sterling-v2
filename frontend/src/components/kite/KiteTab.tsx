import {useExperienceOptions} from '../../store/useExperienceOptions';
import { useMobileBackLayer } from '../../hooks/useMobileBackLayer';
import { useSavedSelection } from '../../hooks/useSavedSelection';
import { notificationDestination } from '../../lib/notificationDestination';
import React, { useState, useEffect, useRef, Suspense, lazy } from 'react';
import { KiteLayout, NavItem, MoreTab } from './KiteLayout';
import { KiteDashboard } from './KiteDashboard';
import { SterlingWatchListWithHoldingsSync } from './SterlingWatchListWithHoldingsSync';
import type { InstrumentTab } from './InstrumentPane';
import { KiteNotifications } from './KiteNotifications';
import { PendingGttProtectionWatcher } from './PendingGttProtectionWatcher';
import { KiteSessionGuard } from './KiteSessionGuard';
import { KiteAuthOverlay } from './KiteLoader';

import type { BoardSignal } from './board/boardTypes';
import { EngineTerminal } from './EngineTerminal';
import { formatInstrument } from '../../lib/instrument';
import { KiteTicker } from './KiteTicker';
import { useKiteAutoSession, useKiteStatus } from '../../hooks/useKite';
const OrderWindow = lazy(() => import('./OrderWindow').then(m => ({ default: m.OrderWindow })));
import { useOrderWindowStore } from '../../store/useOrderWindowStore';
const BasketPane = lazy(() => import('./BasketPane').then(m => ({ default: m.BasketPane })));
import { useKiteBasketStore } from '../../store/useKiteBasketStore';
import { useKiteSettings } from '../../store/useKiteSettings';
import {
  EngineStartupBoundary,
  KiteStartupCoordinator,
  TickerStartupBoundary,
  WatchlistStartupBoundary,
} from './KiteStartupSurfaces';
import { KiteInteractionMotion } from './KiteInteractionMotion';
import { k } from '../../styles/kiteUI';
import { type SignalChartData } from '../../types/kiteEngine';
import { hasUnsavedDraft } from './config/unsavedDraftGuard';
import { openSettingsSection } from './config/registry';
import { AdaptiveEdgeRightSidebar } from './AdaptiveEdgeRightSidebar';

// Detail charts and setup inspectors are heavy, optional surfaces. Load their
// bundles only on interaction; the central content already owns a Suspense
// boundary and the broker execution paths are left eagerly available.
const InstrumentPane = lazy(() => import('./InstrumentPane').then(m => ({ default: m.InstrumentPane })));
const SetupChart = lazy(() => import('./SetupChart').then(m => ({ default: m.SetupChart })));
const SignalDetailPane = lazy(() => import('./SignalDetailPane').then(m => ({ default: m.SignalDetailPane })));
const BoardDetailPane = lazy(() => import('./board/BoardDetailPane').then(m => ({ default: m.BoardDetailPane })));

// Lazy-loaded secondary panes for instant initial workspace mount
const DataLakePane = lazy(() => import('./DataLakePane').then(m => ({ default: m.DataLakePane })));
const MarketDataPane = lazy(() => import('./MarketDataPane').then(m => ({ default: m.MarketDataPane })));
const ConnectPane = lazy(() => import('./ConnectPane').then(m => ({ default: m.ConnectPane })));
const HelpPane = lazy(() => import('./HelpPane').then(m => ({ default: m.HelpPane })));
const MutualFundsPane = lazy(() => import('./MutualFundsPane').then(m => ({ default: m.MutualFundsPane })));
const PortfolioPane = lazy(() => import('./PortfolioPane').then(m => ({ default: m.PortfolioPane })));
const PositionsPane = lazy(() => import('./PositionsPane').then(m => ({ default: m.PositionsPane })));
const OrdersPane = lazy(() => import('./OrdersPane').then(m => ({ default: m.OrdersPane })));
const FundsPane = lazy(() => import('./FundsPane').then(m => ({ default: m.FundsPane })));
const BidsPane = lazy(() => import('./BidsPane').then(m => ({ default: m.BidsPane })));
const AlertsPane = lazy(() => import('./AlertsPane').then(m => ({ default: m.AlertsPane })));
const BacktestPane = lazy(() => import('./BacktestPane').then(m => ({ default: m.BacktestPane })));
const UnifiedBacktestPane = lazy(() => import('../backtest/UnifiedBacktestPane').then(m => ({ default: m.UnifiedBacktestPane })));
const AstroPane = lazy(() => import('./AstroPane').then(m => ({ default: m.AstroPane })));
const PcrPane = lazy(() => import('./PcrPane').then(m => ({ default: m.PcrPane })));
const JournalTab = lazy(() => import('../journal/JournalTab').then(m => ({ default: m.JournalTab })));
const LiveOperationsPanel = lazy(() => import('./LiveOperationsPanel').then(m => ({ default: m.LiveOperationsPanel })));

const PriceRulesPanel = lazy(() => import('./PriceRulesPanel').then(m => ({ default: m.PriceRulesPanel })));

const MORE_TABS: { id: MoreTab; label: string }[] = [
  { id: 'bids', label: 'Bids' },
  { id: 'funds', label: 'Funds' },
  { id: 'mf', label: 'Mutual Funds' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'backtest', label: 'Backtest' },
  { id: 'data', label: 'Data' },
  // The one screen a non-technical operator is expected to read and act on.
  { id: 'operations', label: 'Operations' },
  { id: 'price-rules', label: 'Price Rules' },
];

function MorePane({ activeTab, onTabChange }: { activeTab: MoreTab; onTabChange: (t: MoreTab) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="more-tabs" style={{ display: 'flex', borderBottom: `1px solid ${k.border}`, background: k.bg, flexShrink: 0 }}>
        {MORE_TABS.map((t) => {
          const active = activeTab === t.id;
          return (
            <button
              key={t.id}
              data-testid={`more-tab-${t.id}`}
              onClick={() => onTabChange(t.id)}
              style={{
                padding: '10px 16px', border: 'none', background: 'transparent', cursor: 'pointer',
                fontSize: 13, fontWeight: active ? 600 : 400,
                color: active ? 'var(--k-brand)' : 'var(--k-ink-4)',
                borderBottom: active ? '2px solid var(--k-brand)' : '2px solid transparent',
                marginBottom: -1, transition: 'color 0.15s',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <Suspense fallback={<div style={{ padding: 20, color: 'var(--k-dim)' }}>Loading...</div>}>
          {activeTab === 'bids' && <BidsPane />}
          {activeTab === 'funds' && <FundsPane />}
          {activeTab === 'mf' && <MutualFundsPane />}
          {activeTab === 'alerts' && <AlertsPane />}
          {activeTab === 'backtest' && <BacktestPane />}
          {activeTab === 'data' && <MarketDataPane />}
          {activeTab === 'operations' && <LiveOperationsPanel />}
          {activeTab === 'price-rules' && <PriceRulesPanel />}
        </Suspense>
      </div>
    </div>
  );
}

export function KiteTab() {
  const preferredSection = useKiteSettings((s) => s.defaultSection);
  const params = new URLSearchParams(window.location.search);
  const notificationNav = params.get('view') === 'live' ? notificationDestination(params.get('notification')) : null;
  const userNavigatedRef = useRef(Boolean(notificationNav) || (()=>{try{return Boolean(sessionStorage.getItem('sterling.page.workspace.nav'));}catch{return false;}})());
  const [nav, setNav] = useSavedSelection<NavItem>('workspace.nav', useKiteSettings.getState().defaultSection || 'dashboard', notificationNav);
  const [moreTab, setMoreTab] = useSavedSelection<MoreTab>('workspace.moreTab', 'bids');
  const [instrumentView, setInstrumentView] = useState<{ symbol: string; tab: InstrumentTab; trailTarget?: 'fast' | 'mid' | 'slow'; signalData?: SignalChartData } | null>(null);
  const [setupView, setSetupView] = useState<{ token: number; underlying: string } | null>(null);
  const [detailView, setDetailView] = useState<{ token: number; underlying: string; timestamp_ms: number; source?: string } | null>(null);
  // Engines on the shared board carry their whole record in the signal, so
  // their detail page needs no second fetch and no per-engine component.
  const [boardDetail, setBoardDetail] = useState<BoardSignal | null>(null);
  const [savedTerminalMode, setSavedTerminalMode] = useState<'minimized' | 'normal' | 'partial' | 'full' | null>(null);
  const [basketOpen, setBasketOpen] = useState(false);
  useMobileBackLayer(basketOpen,()=>setBasketOpen(false),'basket');
  const basketCount = useKiteBasketStore((s) => s.entries.length);
  const { data: kiteStatus, isLoading: kiteStatusLoading } = useKiteStatus();
  useKiteAutoSession();

  useEffect(() => {
    if (!userNavigatedRef.current && preferredSection && preferredSection !== nav) {
      setNav(preferredSection);
    }
  }, [preferredSection, nav]);

  const { isOpen, options, closeOrderWindow } = useOrderWindowStore();

  useEffect(() => { window.dispatchEvent(new CustomEvent('kite-page-changed', {detail:nav})); }, [nav]);

  const handleNavClick = (n: NavItem) => {
    const options=useExperienceOptions.getState().options;
    if(options.navHidden.includes(n))return;
    userNavigatedRef.current = true;
    if (n !== nav && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('kite-scroll-to-draft-bar'));
      if (!window.confirm('You have unsaved settings changes. Leave this page and discard them?')) {
        return;
      }
    }
    closeChartView();
    window.dispatchEvent(new CustomEvent('kite-focus-main-pane'));
    setNav(n);
    setSetupView(null);
    setDetailView(null);
    setBoardDetail(null);
  };

  useEffect(() => {
    const cb = (e: Event) => {
      userNavigatedRef.current = true;
      handleNavClick((e as CustomEvent<NavItem>).detail);
    };
    window.addEventListener('kite-nav-click', cb);
    return () => window.removeEventListener('kite-nav-click', cb);
  }, [handleNavClick]);

  useMobileBackLayer(Boolean(instrumentView||setupView||detailView||boardDetail),()=>{closeChartView();setSetupView(null);setDetailView(null);setBoardDetail(null);},'detail-page');
  const mainViewTitle = instrumentView ? `${formatInstrument(instrumentView.symbol)} · ${instrumentView.tab === 'chart' ? 'Chart' : 'Instrument'}` : setupView ? 'Setup chart' : detailView || boardDetail ? 'Signal details' : '';
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('kite-main-view-title', { detail: mainViewTitle }));
    if (mainViewTitle) window.dispatchEvent(new Event('kite-focus-main-pane'));
  }, [mainViewTitle]);

  const handleOpenInstrument = (symbol: string, defaultTab: InstrumentTab | 'chart' | 'option-chain', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: SignalChartData) => {
    if (!instrumentView) {
      const cur = localStorage.getItem('kite_terminal_mode');
      setSavedTerminalMode(cur === 'minimized' || cur === 'partial' || cur === 'full' ? cur : 'normal');
      window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: 'minimized' }));
    }
    // The instrument view renders in the CENTRE slot. If the operator has that pane
    // minimized, it mounts into a collapsed dock and the Chart button looks dead —
    // which is exactly how it was reported. Minimized state persists across reloads,
    // so this is not a transient condition that fixes itself.
    window.dispatchEvent(new CustomEvent('kite-restore-slot', { detail: 'center' }));
    // A detail or setup view outranks the instrument view where `content` is chosen,
    // so opening a chart while either is up would set state that nothing renders.
    // Every other caller clears its siblings; this one did not.
    setDetailView(null);
    setSetupView(null);
    setBoardDetail(null);
    setInstrumentView({ symbol, tab: defaultTab as InstrumentTab, trailTarget, signalData });
  };

  const closeChartView = () => {
    if (savedTerminalMode) {
      window.dispatchEvent(new CustomEvent('kite-terminal-mode', { detail: savedTerminalMode }));
      setSavedTerminalMode(null);
    }
    setInstrumentView(null);
  };

  let content = null;
  if (setupView) {
    content = <SetupChart token={setupView.token} underlying={setupView.underlying} onClose={() => { closeChartView(); setSetupView(null); }} />;
  } else if (boardDetail) {
    content = <BoardDetailPane signal={boardDetail} onClose={() => { closeChartView(); setBoardDetail(null); }} />;
  } else if (detailView) {
    content = (
      <SignalDetailPane
        token={detailView.token}
        underlying={detailView.underlying}
        timestamp_ms={detailView.timestamp_ms}
        source={detailView.source}
        onClose={() => { closeChartView(); setDetailView(null); }}
        onShowSetup={() => setSetupView(detailView)}
        onOpenChart={(symbol) => handleOpenInstrument(symbol, 'chart')}
        onShowOptionChain={(u) => handleOpenInstrument(u, 'option-chain')}
      />
    );
  } else if (instrumentView) {
    content = (
      <InstrumentPane
        onClose={closeChartView}
        symbol={instrumentView.symbol}
        initialTab={instrumentView.tab}
        trailTarget={instrumentView.trailTarget}
        signalData={instrumentView.signalData}
        onSymbolChange={(newSymbol) => setInstrumentView({ symbol: newSymbol, tab: 'chart', trailTarget: instrumentView.trailTarget, signalData: instrumentView.signalData })}
      />
    );
  } else {
    if (nav === 'dashboard') content = <KiteDashboard />;
    else if (nav === 'astro') content = <AstroPane />;
    else if (nav === 'pcr') content = <PcrPane />;
    else if (nav === 'orders') content = <OrdersPane onOpenBasket={() => setBasketOpen(true)} />;
    else if (nav === 'holdings') content = <PortfolioPane view="holdings" />;
    else if (nav === 'positions') content = <PositionsPane onOpenInstrument={handleOpenInstrument} />;
    else if (nav === 'journal') content = <JournalTab />;
    else if (nav === 'more') content = <MorePane activeTab={moreTab} onTabChange={setMoreTab} />;
    else if (nav === 'data') content = <DataLakePane />;
    else if (nav === 'backtest') content = <UnifiedBacktestPane />;
    else if (nav === 'connect') content = <ConnectPane />;
    else if (nav === 'help') content = <HelpPane />;
  }

  return (
    <KiteInteractionMotion>
        <KiteStartupCoordinator statusLoading={kiteStatusLoading} hasStatus={!!kiteStatus} />
        <KiteLayout
          activeNav={nav}
          mainViewTitle={mainViewTitle}
          onNavClick={handleNavClick}
          sidebar={nav === 'dashboard' ? (
            <WatchlistStartupBoundary>
              <SterlingWatchListWithHoldingsSync onOpenInstrument={handleOpenInstrument} />
            </WatchlistStartupBoundary>
          ) : null}
          rightSidebar={nav === 'dashboard' ? (
            <EngineStartupBoundary>
              <AdaptiveEdgeRightSidebar
                onSelectSignal={(sel) => { setInstrumentView(null); setSetupView(null); setDetailView(sel); }}
                onOpenBoardDetail={(signal) => { setInstrumentView(null); setSetupView(null); setDetailView(null); setBoardDetail(signal); }}
                onOpenChart={handleOpenInstrument}
              />
            </EngineStartupBoundary>
          ) : null}
          bottomBar={nav === 'dashboard' ? <EngineTerminal /> : null}
          centerTopBar={nav === 'dashboard' ? (
            <div className="workspace-market-strip" data-page={nav}><TickerStartupBoundary>
              <KiteTicker onOpenChart={(symbol) => handleOpenInstrument(symbol, 'chart')} />
            </TickerStartupBoundary></div>
          ) : null}
          content={<Suspense fallback={<div style={{ padding: 20, color: 'var(--k-dim)' }}>Loading...</div>}>{content}</Suspense>}
          onBasketClick={() => setBasketOpen(true)}
          basketCount={basketCount}
        />
        <KiteNotifications />
        <PendingGttProtectionWatcher />
        <KiteSessionGuard onOpenAccountSettings={() => openSettingsSection('account')} />
        <KiteAuthOverlay />
        <Suspense fallback={<div role="status">Loading order controls…</div>}>
        {isOpen && options && <OrderWindow options={options} onClose={closeOrderWindow} />}
        {basketOpen && <BasketPane onClose={() => setBasketOpen(false)} />}
        </Suspense>
    </KiteInteractionMotion>
  );
}

export default KiteTab;

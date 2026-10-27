import {useMobileBackLayer} from '../../hooks/useMobileBackLayer';
import {toLayoutPx,layoutViewport} from '../../styles/applyViewportScale';
import React from 'react';
import { stamp, barCloseMs, isDayExpandedByDefault, sessionDayKey, shiftSessionDay, underlyingQuoteKey, parseTimestampMs, formatSessionDay } from './board/boardTypes';
import { createPortal } from 'react-dom';
import { k, tint } from '../../styles/kiteUI';
import { EngineToolbar, ToolbarButton } from './board/EngineToolbar';
import { ColumnsMenu, FilterToggle, ModeFilter } from './board/BoardFilters';
import { ViewFiltersDisclosure } from './board/ViewFiltersDisclosure';
import { closedReasonText, supersededLegKeys } from './board/supertrendAdapter';
import { normalizeMode, MODE_LABEL, type TradeMode } from './board/boardTypes';
import { useSavedSelection } from '../../hooks/useSavedSelection';
// The row's geometry and columns now live beside the shared board, so every
// engine renders against the same table rather than a copy of it.
import { HEAD_METRICS, DAY_HEAD_METRICS, LEG_BG, LEG_INDENT,
  ROW_METRICS, SIGNAL_LEFT_COLUMNS, SIGNAL_RIGHT_COLUMNS,
  type SignalColVisibility,
} from './board/signalRowSpec';
import { DraggableColHeader, makeHscrollSync } from './board/tableMechanics';
import { instrumentFlex } from './board/signalRowSpec';
import { SuperTrendSharedBoard } from './SuperTrendSharedBoard';
import { StrategyBoardFooter } from './board/StrategyBoardFooter';
import { useSimNowMs } from '../../hooks/useReplayStore';
import { useEngineConfig, useEngineSignals, useRunScan, usePatchEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useTodaySignalPriority } from './board/TodaySignalPriority';
import type { EngineConfigModel, EngineSignalRow, SignalsResponse, SignalChartData } from '../../types/kiteEngine';
import { useKiteQuote } from '../../hooks/useKite';
import { InstrumentLabel } from './InstrumentLabel';
import { Tip } from './InfoTooltip';
import { KiteLoader } from './KiteLoader';
import { Icons } from '../../styles/kiteUI';
import { QuoteDetail, KiteSearchBar } from './SterlingWatchList';
import { KiteActionButtons } from './KiteActionButtons';
import { computeGreeksFromLeg } from '../../utils/computeGreeks';
import { stopDistance, selectBestLegs, type LegCandidate } from './impactMath';
import { notifyOrder } from '../../store/useKiteNotifications';
import { type BoardCapabilityKey, useKiteSettings } from '../../store/useKiteSettings';
import { useOrderWindowStore } from '../../store/useOrderWindowStore';
import { useTickerPins } from '../../store/useTickerPins';
import { useLiveSignalCount } from '../../store/useLiveSignalCount';
import { useSignalMarkers, type Marker } from '../../store/useSignalMarkers';
import { signalChartDataForPremiumLeg } from '../charts/signalMarkerLogic';
import { AdaptiveEdgePositionCalculator } from './AdaptiveEdgePositionCalculator';
import { useEffectiveNowMs } from '../../hooks/useReplayStore';

import { fmtTick, roundToTick } from '../../utils/fmt';
import { EXIT_MODE_OPTIONS, SCAN_SOURCE_OPTIONS, needsRescan, openSettingsSection } from './config/registry';
import { PaneHeaderActions } from './PaneHeaderActions';

interface Props {
  // `source` travels with the click: a Navigator origination and a SuperTrend row
  // for the same instrument share a token, so the detail request needs it to open
  // the row the user actually clicked.
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  onOpenChart?: (symbol: string, tab: 'chart', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: SignalChartData) => void;
}

// Exit counter modes and scan sources come from the shared config registry, so
// the board header and the settings pages cannot disagree about what a value is
// called. They used to be declared here as well, and had already drifted — the
// same scan_source was "Derivatives" on one page and "Options" on another.
const EXIT_MODE_OPTS = EXIT_MODE_OPTIONS;
const SCAN_SOURCE_OPTS = SCAN_SOURCE_OPTIONS;

// Release UI is deliberately SuperTrend-only. The broader union remains on legacy
// row-renderer props so historical payloads can still be decoded, but this pane
// never selects or exposes the retired Navigator lenses.
type SignalMode = 'supertrend' | 'navigator' | 'combined' | 'common';

// Granular universe pickers. `name` is the value stored in config (matches the
// backend UniverseItem display name); `label` is the short chip text.
const INDEX_OPTS: { name: string; label: string }[] = [
  { name: 'NIFTY 50', label: 'NIFTY' },
  { name: 'NIFTY BANK', label: 'BANKNIFTY' },
  { name: 'NIFTY FIN SERVICE', label: 'FINNIFTY' },
  { name: 'SENSEX', label: 'SENSEX' },
];

function countdown(ms: number): string {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((ms - Date.now()) / 1000));
  if (s <= 0) return 'due';
  return s >= 60 ? `${Math.floor(s / 60)}m` : `${s}s`;
}

function readTableFlag(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === 'true';
  } catch { return fallback; }
}
function saveTableFlag(key: string, value: boolean): void {
  try { localStorage.setItem(key, String(value)); } catch { /* storage may be unavailable */ }
}

export function Arrow({ v }: { v: number }) {
  const flat = v === 0;
  return <span style={{ color: flat ? k.dim : v > 0 ? k.green : k.red, fontSize: 11, fontWeight: 700 }}>{flat ? '·' : v > 0 ? '▲' : '▼'}</span>;
}

export function SortHeaderDiv({ label, sortKey, sort, handleSort, style, align = 'left' }: any) {
  const isActive = sort.key === sortKey && sort.dir !== '';
  return (
    <div
      // The active column's heading brightens, as it does on the shared board:
      // the sort arrow alone is a 8x4 glyph, which is not enough to say which
      // column the table is ordered by. Everything else about the type is
      // inherited from the header strip.
      style={{ ...style, color: isActive ? k.text : undefined, cursor: 'pointer', userSelect: 'none' }}
      onClick={() => handleSort(sortKey)}
      // Sortable from the keyboard, and carrying the shared board's focus ring.
      // This was a plain div with an onClick: the only way to reorder this table
      // was with a mouse. `sb-head` is the shared heading class, so the ring is
      // defined once for both tables; the sort-glyph hover stays local because
      // this table's glyph is not the same element as the shared board's.
      className={sortKey ? "sort-header-div sb-head" : ""}
      role={sortKey ? 'button' : undefined}
      tabIndex={sortKey ? 0 : undefined}
      aria-label={sortKey ? `Sort by ${label}` : undefined}
      onKeyDown={sortKey ? (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        handleSort(sortKey);
      } : undefined}
      title={`Sort by ${label}`}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: align === 'right' ? 'flex-end' : 'flex-start' }}>
        {label}
        {sortKey && (
          <span className={`sort-icon ${isActive ? 'active' : ''}`}>
             <svg width="8" height="4" viewBox="0 0 8 4" fill={isActive && sort.dir === 'asc' ? 'var(--k-blue-kite)' : 'currentColor'} style={{ opacity: (!isActive || sort.dir === 'asc') ? 1 : 0.2 }}><path d="M4 0L8 4H0L4 0Z"/></svg>
             <svg width="8" height="4" viewBox="0 0 8 4" fill={isActive && sort.dir === 'desc' ? 'var(--k-blue-kite)' : 'currentColor'} style={{ opacity: (!isActive || sort.dir === 'desc') ? 1 : 0.2 }}><path d="M4 4L8 0H0L4 4Z"/></svg>
          </span>
        )}
      </div>
    </div>
  );
}

/** Single source of truth for the signal-table column widths/labels — read by
 *  BOTH the list-view header and each leg row so they can never drift out of
 *  alignment (previously each side hand-typed its own copy of every width).
 *  Split into two groups matching the table's two flex sections: LEFT flows
 *  next to the (flex:1) Instrument column; RIGHT is pinned after the action
 *  buttons. `visibleWhen` is a tag both the header and the row resolve against
 *  their own (equivalent, differently-named) boolean for that condition. */

/**
 * Rows and the header keep their sideways scroll in step.
 *
 * Built here, now shared: `makeHscrollSync` lives in board/tableMechanics so the
 * shared board can offer the same thing. The selector names only rows that opted
 * into scrolling, so a board with the setting off is not reached at all.
 */
const syncHscroll = makeHscrollSync('.st-header-row, .st-row-scroll');

// A long option leg has EXITED once the last scan flagged its SuperTrend as no longer
// aligned (`is_active` false) OR — between scans, while that flag is frozen — once the
// LIVE premium has fallen to/through its trailing stop. The cached flag and the entry
// snapshot are taken at scan time, but the LTP keeps ticking; reconciling against the
// live price stops a collapsed position from lingering as "running" with a frozen entry
// sitting next to a wildly different live LTP (the classic entry-971 / LTP-193 gap).
// `premium_sl` is the trail snapshot AT ENTRY (computed on Heikin-Ashi premium), so this
// is a conservative check — it only flips to exited when the live price is clearly below
// it; the next scan recomputes the authoritative trailing exit.
function legHasExited(
  leg: any, rowActive: boolean | undefined, ltp: number | null | undefined,
): boolean {
  const cachedActive = (leg?.is_active ?? rowActive) ?? false;
  const stop = leg?.premium_sl;
  const liveExited = ltp != null && stop != null && stop > 0 && ltp <= stop;
  return !cachedActive || liveExited;
}

function rowIsRunning(row: EngineSignalRow, quotes: any): boolean {
  // Gated on whether the row actually CARRIES a premium plan, not on source ==
  // 'derivatives'. A confluence row carries one too (the card's own `hasPremium` says
  // so), and it was excluded here — so the "Active now" bucket and the footer
  // live-count used the frozen scan flag while the card body reconciled every leg
  // against the live LTP. The row said running while its own legs all read ended.
  if (!hasPremiumSnapshot(row)) return rowIsLive(row);
  return row.legs.some(
    (l) => !legHasExited(l, rowIsLive(row), quotes?.[`${row.exchange}:${(l as any).option_symbol}`]?.last_price ?? null),
  );
}

// A row is LIVE while EITHER flag is set. The backend's only way to end a row is
// to clear both, so reading `is_active` alone mislabels a signal that is fresh
// but not yet marked active — which is exactly how a Navigator origination
// arrives on its first bar. Such a row rendered struck-through as history the
// instant it appeared.
function rowIsLive(row: { is_active?: boolean; is_fresh?: boolean }): boolean {
  return !!(row.is_active || row.is_fresh);
}

// True only when the greeks were solved from a real IV. `blackScholesGreeks` falls back
// to an intrinsic-only delta (exactly ±1.00 or 0, with gamma/theta/vega all zero) when
// no IV could be found or back-solved, which is a "no data" answer wearing the costume
// of a very confident one. Anything ranked or displayed as a delta must exclude those.
function hasUsableGreeks<T extends { iv: number }>(g: T | null | undefined): g is T {
  return !!g && g.iv > 0;
}

// The live premium has already traded through this leg's trailing stop, yet the engine
// still counts the leg as running — the SuperTrend exit is a RED-COUNTER rule, so a
// position can sit well past its trail until enough ST lines flip. Surfacing this is
// the difference between "your trail is doing its job" and a silent open drawdown.
function legStopBreached(leg: any, ltp: number | null | undefined, ended: boolean): boolean {
  if (ended) return false;
  const stop = leg?.premium_sl;
  return ltp != null && stop != null && stop > 0 && ltp <= stop;
}

function hasPremiumSnapshot(row: EngineSignalRow): boolean {
  return row.legs.some((leg) => (
    ((leg as any).premium_spot ?? 0) > 0
    || ((leg as any).premium_sl ?? 0) > 0
    || ((leg as any).entry_sl ?? 0) > 0
  ));
}

// Coarse moneyness group (ITM1-5 / ATM / OTM1-5 → ITM / ATM / OTM), shared by the
// per-bucket best-R:R/delta ranking and the "Best only" display order below.
function moneynessBucket(m: string | undefined): 'ITM' | 'ATM' | 'OTM' {
  if (m === 'ATM') return 'ATM';
  return m?.startsWith('ITM') ? 'ITM' : 'OTM';
}
const MONEYNESS_GROUP_ORDER: Record<'ITM' | 'ATM' | 'OTM', number> = { ITM: 0, ATM: 1, OTM: 2 };

/**
 * Whether a signal-table column is rendered.
 *
 * Two separate questions, which the old single map conflated.
 *
 * **Capability** — can the column be filled at all? `premium` is the only real
 * one: Entry, SL, TSL and Target need a scan source that produces premiums, and
 * a spot scan has none. That is not a preference and must not appear in a menu.
 *
 * **Preference** — does the operator want it? Per column, by key.
 *
 * Before this, `visibleWhen` did both, so four columns hid behind one `premium`
 * flag and Exit and LTP were `always` with no way to switch them off. A COLUMNS
 * menu built on that could only offer six abstract groups, which is why it did
 * not list the columns the table actually shows.
 */
function signalColCapable(visibleWhen: SignalColVisibility, premiumAvailable: boolean): boolean {
  return visibleWhen === 'premium' ? premiumAvailable : true;
}

function signalColShown(
  col: { key: string; visibleWhen: SignalColVisibility },
  premiumAvailable: boolean,
  hidden: readonly string[],
  /**
   * Whether the row is carrying its order buttons.
   *
   * Trade and Chart answer to TWO controls: the column picker, and the older
   * "order buttons in the row" switch whose description promises they MOVE into
   * the expanded row rather than disappear. Either one can withhold them, and
   * both the header and the cells go through this function — which is the only
   * reason the headings can be trusted to sit over their own columns.
   */
  rowActionsOn = true,
): boolean {
  if (!rowActionsOn && (col.key === 'trade' || col.key === 'chart')) return false;
  return signalColCapable(col.visibleWhen, premiumAvailable) && !hidden.includes(col.key);
}

// ─────────────────────────────────────────────────────────────────────────────
// Console-header building blocks (clean console + collapsible settings drawer)
// ─────────────────────────────────────────────────────────────────────────────

/** What this engine is actually scanning, read off the live config.
 *
 *  This used to be a constant asserting "Nifty50, BankNifty, FinNifty & Sensex
 *  constituents" no matter what was configured — so a board scanning one index
 *  still claimed to cover four. */
function universeTip(cfg?: EngineConfigModel | null): string {
  if (!cfg) return 'Scans the configured instruments on the 1H timeframe.';
  const indices = cfg.scan_indices.length;
  const stocks = cfg.scan_stock_contracts === false
    ? 'no stocks'
    : cfg.scan_all_stocks
      ? 'all eligible F&O stocks'
      : `${cfg.scan_stocks.length} stock${cfg.scan_stocks.length === 1 ? '' : 's'}`;
  const strikes = cfg.strike_moneyness.length;
  const source = SCAN_SOURCE_OPTIONS.find((o) => o.value === cfg.scan_source)?.label ?? cfg.scan_source;
  return `Scans ${indices} ${indices === 1 ? 'index' : 'indices'} + ${stocks}, `
    + `${strikes} strike${strikes === 1 ? '' : 's'} each, from the ${source} chart on the 1H timeframe.`;
}

/** The quotes-map key for a row's underlying.
 *  SENSEX/BANKEX are BSE, and the index short names are stored under their full
 *  display names. Shared by the row rendering and the board sort so the two
 *  cannot disagree about which quote a row means. */
function RefreshIcon({ spinning }: { spinning?: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      className={spinning ? 'st-spin' : undefined}>
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7"></rect>
      <rect x="14" y="3" width="7" height="7"></rect>
      <rect x="14" y="14" width="7" height="7"></rect>
      <rect x="3" y="14" width="7" height="7"></rect>
    </svg>
  );
}

function ListIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6"></line>
      <line x1="8" y1="12" x2="21" y2="12"></line>
      <line x1="8" y1="18" x2="21" y2="18"></line>
      <line x1="3" y1="6" x2="3.01" y2="6"></line>
      <line x1="3" y1="12" x2="3.01" y2="12"></line>
      <line x1="3" y1="18" x2="3.01" y2="18"></line>
    </svg>
  );
}

// Three stepped trend strokes — a quiet nod to fast / mid / slow SuperTrend.
function EngineMark() {
  return (
    <span aria-hidden style={{ display: 'inline-flex', flexDirection: 'column', gap: 2, width: 15, flexShrink: 0 }}>
      <span style={{ height: 2.5, borderRadius: 2, background: k.orange, width: '100%' }} />
      <span style={{ height: 2.5, borderRadius: 2, background: tint(k.orange, 55), width: '68%' }} />
      <span style={{ height: 2.5, borderRadius: 2, background: tint(k.orange, 32), width: '40%' }} />
    </span>
  );
}

function Switch({ on, onChange, color, label }: { on: boolean; onChange: () => void; color: string; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={onChange}
      style={{
        position: 'relative', width: 34, height: 19, borderRadius: 999, border: 'none', padding: 0,
        cursor: 'pointer', flexShrink: 0, background: on ? color : k.border, transition: 'background .18s ease',
      }}>
      <span style={{
        position: 'absolute', top: 2, left: on ? 17 : 2, width: 15, height: 15, borderRadius: '50%',
        background: 'var(--k-bg)', boxShadow: '0 1px 2px rgba(0,0,0,.25)', transition: 'left .18s ease',
      }} />
    </button>
  );
}

// Pill toggle for the granular universe pickers (multi-select chips).
function Chip({ label, active, onClick, dim }: { label: string; active: boolean; onClick: () => void; dim?: boolean }) {
  return (
    <button onClick={onClick} aria-pressed={active} title={dim ? 'Turn off “All F&O” to pick individual stocks' : label}
      style={{
        fontSize: 10.5, fontWeight: active ? 600 : 500, padding: '3px 9px', borderRadius: 999,
        cursor: dim ? 'default' : 'pointer', whiteSpace: 'nowrap', transition: 'all .14s ease',
        border: `1px solid ${active ? k.orange : k.border}`,
        background: active ? tint(k.orange, 12) : k.bg,
        color: active ? k.orange : (dim ? k.dim : k.text), opacity: dim ? 0.5 : 1,
      }}>
      {label}
    </button>
  );
}

// Collapsible labeled card for the 'cards' layout.
function Collapsible({ label, summary, open, onToggle, children }: {
  label: string; summary?: string; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div style={{ border: `1px solid ${k.border}`, borderRadius: 7, overflow: 'hidden', background: k.bg }}>
      <button onClick={onToggle} aria-expanded={open}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
          padding: '8px 11px', cursor: 'pointer', border: 'none', background: open ? k.surfaceHover : k.surface,
          textAlign: 'left', transition: 'background .15s ease',
        }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, textTransform: 'uppercase', color: k.text }}>{label}</span>
          {!open && summary && <span style={{ fontSize: 10, color: k.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summary}</span>}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform .2s ease', color: k.dim, flexShrink: 0 }}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && <div style={{ padding: '11px 12px 13px', borderTop: `1px solid ${k.border}`, display: 'flex', flexDirection: 'column', gap: 11 }}>{children}</div>}
    </div>
  );
}

/**
 * How this board looks.
 *
 * Deliberately not what it shows: Best leg and Ended are live filters and live
 * in the toolbar, where their state is visible without opening a panel. This
 * used to carry a second copy of both as checkboxes, which is two controls for
 * one setting and a pair that will eventually drift.
 *
 * "Reset board view" therefore resets appearance only. A button in an
 * appearance panel that silently un-filters your list is a surprise.
 */
/**
 * Board display settings, for every engine.
 *
 * Exported and prop-free because it opens from the pane's TITLE BAR now, which
 * is rendered by the engine-tab shell rather than by any one engine's board.
 * Every setting in here already lived in the shared store — columns, the three
 * behaviours, the renderer — so nothing about it was SuperTrend-specific except
 * where it happened to be mounted. The layout choice was the last hold-out and
 * has moved into the store as well.
 */
export function SignalTableSettingsPanel() {
  const settings = useKiteSettings();
  const viewLayout = settings.signalViewLayout;
  const onLayoutChange = settings.setSignalViewLayout;
  const columns: Array<{ key: 'showExchange' | 'showLeg' | 'showPriceChange' | 'showPriceChangePct' | 'showPriceDirection'; label: string; hint: string }> = [
    { key: 'showExchange', label: 'Exchange', hint: 'NSE, NFO or BFO badge' },
    { key: 'showLeg', label: 'Leg', hint: 'ATM, ITM or OTM label' },
    { key: 'showPriceChange', label: 'Change', hint: 'Absolute price change' },
    { key: 'showPriceChangePct', label: 'Change %', hint: 'Percentage price change' },
    { key: 'showPriceDirection', label: 'Direction', hint: 'Up/down direction indicator' },
  ];

  const behaviours: Array<{ key: BoardCapabilityKey; label: string; hint: string }> = [
    {
      key: 'boardDragColumns', label: 'Drag columns to reorder',
      hint: 'Drag a column heading sideways to move it. Off leaves the heading a plain sort control.',
    },
    {
      key: 'boardRowScroll', label: 'Scroll rows sideways',
      hint: 'Each row scrolls horizontally on its own when the board is narrower than its columns. Off keeps every row aligned and relies on hiding columns instead.',
    },
    {
      key: 'boardRowActions', label: 'Order buttons in the row',
      hint: 'Buy, chart and pin sit in the row itself. Off moves them into the row you expand — fewer controls under the pointer, one more click to trade.',
    },
  ];

  const reset = () => {
    settings.resetSignalTableSettings();
    onLayoutChange('list');
  };

  // One navigation helper for the whole app. There used to be two incompatible
  // channels — this one wrote localStorage and fired 'kite-nav-click' (a no-op
  // when the settings pane was already mounted), while the settings panels fired
  // 'kite-connect-section' (a no-op when it was not). openSettingsSection does
  // both, and validates the id.
  const openTradeRules = () => openSettingsSection('manualRules');

  return (
    <div style={{ padding: '16px 18px 18px', background: k.bg, borderBottom: `1px solid ${k.border}` }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, marginBottom: 15 }}>
        <div>
          {/* Not "SuperTrend board settings" any more: this drawer opens from the pane's
              title bar and every setting in it governs whichever board is showing. */}
          <div style={{ color: k.text, fontSize: 13.5, fontWeight: 750 }}>Board settings</div>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 10.5, lineHeight: 1.5, marginTop: 3 }}>
            How this board looks — nothing here changes what is scanned or how a trade exits. Best leg and
            Ended are live filters and sit in the toolbar above. Entry, stop, exit and sizing rules live
            under Connect → Trade Rules.
          </div>
        </div>
        <button type="button" onClick={openTradeRules} style={{ minHeight: 34, flexShrink: 0, border: `1px solid ${k.border}`, borderRadius: 7, background: k.bg, color: k.text, padding: '0 11px', fontSize: 10.5, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer' }}>
          Trade rules ↗
        </button>
      </div>

      <div className="sk-table-settings-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, .8fr) minmax(250px, 1.5fr)', border: `1px solid ${k.border}`, borderRadius: 8, overflow: 'hidden' }}>
        <div className="sk-table-settings-group" style={{ padding: 13 }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 9 }}>Layout</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, padding: 3, border: `1px solid ${k.border}`, borderRadius: 8, background: k.surface }}>
            {([
              { value: 'list' as const, label: 'List', icon: <ListIcon /> },
              { value: 'grid' as const, label: 'Grid', icon: <GridIcon /> },
            ]).map((option) => {
              const selected = viewLayout === option.value;
              return (
                <button key={option.value} type="button" title={`${option.label} layout`} aria-pressed={selected} onClick={() => onLayoutChange(option.value)} style={{
                  minHeight: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  border: 'none', borderRadius: 6,
                  background: selected ? k.bg : 'transparent', color: selected ? k.text : 'var(--k-ink-5)',
                  boxShadow: selected ? `inset 0 -2px ${k.orange}, 0 1px 2px rgba(0,0,0,.08)` : 'none',
                  padding: '0 8px', fontSize: 10.5, fontWeight: selected ? 700 : 550, fontFamily: 'inherit', cursor: 'pointer',
                }}>
                  {option.icon}{option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="sk-table-settings-group" style={{ padding: 13, borderLeft: `1px solid ${k.border}` }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 7 }}>Visible columns</div>
          {/* The same five flags are also editable from the sliders button in the
              search bar, which is the only place the watchlist has. Both write one
              store, so they cannot drift — but a user who changes them here should
              not be surprised to find the watchlist changed too. */}
          <div style={{ color: k.dim, fontSize: 9.5, lineHeight: 1.45, marginBottom: 8 }}>
            Shared with the watchlist — one setting, two places to reach it.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '2px 8px' }}>
            {columns.map((column) => (
              <label key={column.key} title={column.hint} style={{ minHeight: 28, display: 'flex', alignItems: 'center', gap: 7, color: k.text, fontSize: 10.5, padding: '3px 2px', cursor: 'pointer' }}>
                <input type="checkbox" checked={settings[column.key]} onChange={() => settings.toggleShow(column.key)} style={{ width: 14, height: 14, margin: 0, accentColor: k.orange }} />
                {column.label}
              </label>
            ))}
          </div>
        </div>

        <div className="sk-table-settings-group" style={{ padding: 13, borderLeft: `1px solid ${k.border}` }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 7 }}>Behaviour</div>
          {/* The three things this table has that the shared board does not.
              Offered as choices rather than kept as one table's habits: that is
              what lets every engine's board have them, instead of only the one
              that happens to render through this component. */}
          <div style={{ color: k.dim, fontSize: 9.5, lineHeight: 1.45, marginBottom: 8 }}>
            Applies to this board. Turning one off does not change what is scanned.
          </div>
          <div style={{ display: 'grid', gap: '2px 8px' }}>
            {behaviours.map((option) => (
              <label key={option.key} title={option.hint} style={{ minHeight: 28, display: 'flex', alignItems: 'center', gap: 7, color: k.text, fontSize: 10.5, padding: '3px 2px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={settings[option.key]}
                  onChange={() => settings.toggleBoardCapability(option.key)}
                  style={{ width: 14, height: 14, margin: 0, accentColor: k.orange }}
                />
                {option.label}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 11 }}>
        <span style={{ color: k.dim, fontSize: 9.5 }}>
          {settings.boardDragColumns
            ? 'In List view, drag column headers to reorder them.'
            : 'Column dragging is off — headers still sort when clicked.'}
        </span>
        <button type="button" onClick={reset} style={{ minHeight: 30, border: `1px solid ${k.border}`, borderRadius: 6, background: k.bg, color: 'var(--k-ink-4)', padding: '0 10px', fontSize: 10, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer' }}>
          Reset board view
        </button>
      </div>
      <style>{`
        @media (max-width: 720px) {
          .sk-table-settings-grid { grid-template-columns: 1fr !important; }
          .sk-table-settings-group { border-left: none !important; border-top: 1px solid ${k.border}; }
          .sk-table-settings-group:first-child { border-top: none; }
        }
      `}</style>
    </div>
  );
}

function InlineDropdown<T extends string>({
  value, options, onChange, tone, title,
  label, scope = 'local',
}: {
  value: T;
  options: { value: T; label: string; hint?: string; disabled?: boolean }[];
  onChange: (next: T) => void;
  tone: string;
  title: string;
  /** The control's own name, rendered INSIDE the chip as COLUMNS does. */
  label?: string;
  /**
   * Whether this setting reaches the server or only this browser.
   *
   * It matters more here than most places: SOURCE and EXIT are saved server-side
   * and change how LIVE trades close, for everyone, while VIEW is a lens over
   * rows that are already there. That difference used to be carried by the
   * coloured labels floating outside each control — moving the names inside made
   * every chip look alike, and looking alike is wrong for two controls where one
   * can close a position and the other cannot.
   *
   * A `server` chip therefore keeps a standing tint and a toned border; a `local`
   * one is plain until opened. The divider still separates the two groups, and
   * the tooltip still spells it out; this is the version you can see without
   * hovering anything.
   */
  scope?: 'server' | 'local';
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  const rect = ref.current?.getBoundingClientRect();
  useMobileBackLayer(open,()=>setOpen(false),'signal-dropdown');

  React.useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const current = options.find((option) => option.value === value);

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      {/* One chip family across the whole toolbar.
          This was a borderless `border-radius: 999` pill sitting beside COLUMNS,
          BEST LEG and ENDED, which are 22px bordered chips at radius 4 — three
          controls of one shape and three of another, on one row. It now matches
          them, and carries its own NAME the way COLUMNS does ("COLUMNS 12/13"),
          so the label no longer floats outside as separate coloured text. */}
      <button type="button" title={title} aria-haspopup="listbox" aria-expanded={open}
        className="sb-tool"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, height: 22, padding: '0 7px',
          border: `1px solid ${open || scope === 'server' ? tint(tone, 45) : k.border}`,
          borderRadius: 4,
          background: open ? tint(tone, 16) : scope === 'server' ? tint(tone, 8) : 'transparent',
          color: tone, fontSize: 9, fontWeight: 700, letterSpacing: '.05em',
          fontFamily: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap',
        }}>
        {label && (
          /* The control's own name, inside the chip. Dimmer than the value: the
             value is what you read, the name is what tells you what it means. */
          <span style={{ color: k.dim, fontWeight: 700 }}>{label}</span>
        )}
        {current?.label ?? value}
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .15s ease' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && createPortal(
        <div ref={menuRef} className="signal-select-menu" role="listbox" aria-label={title} style={{
          position: 'fixed', top: toLayoutPx(rect?.bottom ?? 0) + 4, left: Math.max(8, Math.min(toLayoutPx(rect?.left ?? 0), layoutViewport().width - 228)), zIndex: 100020, width: 210, maxHeight: '55vh',
          background: k.bg, border: `1px solid ${k.border}`, borderRadius: 8, boxShadow: '0 6px 18px rgba(0,0,0,.18)',
          overflow: 'auto', padding: 4,
        }}>
          {options.map((option) => {
            const selected = option.value === value;
            const disabled = Boolean(option.disabled);
            return (
              <button key={option.value} type="button" role="option" aria-selected={selected}
                aria-disabled={disabled}
                disabled={disabled}
                onClick={() => {
                  if (disabled) return;
                  onChange(option.value);
                  setOpen(false);
                }}
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8, width: '100%', textAlign: 'left',
                  border: 'none', borderRadius: 5, background: selected ? tint(tone, 8) : 'transparent',
                  color: disabled ? k.dim : k.text,
                  opacity: disabled ? 0.45 : 1,
                  padding: '7px 8px', fontFamily: 'inherit',
                  cursor: disabled ? 'not-allowed' : 'pointer',
                }}>
                <span style={{ width: 12, flexShrink: 0, color: tone, fontSize: 11, fontWeight: 700 }}>{selected ? '✓' : ''}</span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, fontWeight: selected ? 700 : 600 }}>
                    {option.label}
                    {disabled && (
                      <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 4px', borderRadius: 3, background: tint(k.red, 15), color: k.red }}>
                        Disabled
                      </span>
                    )}
                  </span>
                  {option.hint && <span style={{ display: 'block', marginTop: 1, fontSize: 9.5, color: k.dim, lineHeight: 1.35 }}>{option.hint}</span>}
                </span>
              </button>
            );
          })}
        </div>, document.body
      )}
    </div>
  );
}

// Thin progress bar that ticks independently so the rest of the pane doesn't re-render every second.
/*
 * `ScanProgressBar` lived here: a 2px band above the search row, showing an
 * indeterminate stripe while scanning and a countdown fill otherwise.
 *
 * It spent a whole row of a dense dock restating what the rescan button can
 * say in the space it already occupies — see `ScanProgressRing`, which shows
 * the countdown as a dial with its percentage and reserves motion, not a
 * number, for a scan in flight.
 */

export function SterlingKiteEnginePane({ onSelectSignal, onOpenChart }: Props) {
  const simNowMs = useSimNowMs();
  const effectiveNowMs = useEffectiveNowMs();
  const currentTodayKey = sessionDayKey(effectiveNowMs);
  const isHistoricalSim = simNowMs != null;
  const currentSimDayLabel = isHistoricalSim ? formatSessionDay(currentTodayKey) : 'Today';
  const s = useKiteSettings();
  const priority = useTodaySignalPriority();
  const { data: signals, isLoading: signalsLoading } = useEngineSignals(!priority.olderReady && !isHistoricalSim);
  const { data: cfg } = useEngineConfig();
  const setCfg = usePatchEngineConfig();
  const scan = useRunScan();
  const scanPending = scan.isPending;
  /**
   * Re-scan after the operator switches SuperTrend back on.
   *
   * This used to fan out to all five engines through `scanAll`, with its own copy
   * of the engine list — and that copy predated the re-scan selection, so it
   * ignored both the operator's exclusions and whether the other engines were
   * even running. The dock's re-scan button reads `enabledToScan`, which ANDs the
   * selection with the running switch; this path read neither, so the same
   * platform honoured the setting from one control and not the other.
   *
   * It is now what it always meant: the engine you just turned on gets scanned.
   * There is no list to keep in step, and nothing to exclude — the operator has
   * just explicitly asked for this one.
   */
  const rescanSupertrend = () => {
    if (scanPending) return;
    scan.mutate();
  };
  // Scan cancellation lives beside the shared re-scan button in the dock.

  const [query, setQuery] = React.useState('');
  const [searchSettingsOpen, setSearchSettingsOpen] = React.useState(false);

  const [settingsOpen, setSettingsOpen] = React.useState(false);
  // From the shared store, not local state. The settings drawer that changes it
  // is common to every engine and opens from the pane's title bar, so the value
  // has to live somewhere both can reach.
  const viewLayout = s.signalViewLayout;
  const setViewLayout = s.setSignalViewLayout;

  // Retired strategy lenses are not a release control. Ignore any old
  // `kite_st_signal_mode` value that may still exist in localStorage.
  const signalMode = 'supertrend' as SignalMode;
  const legSort = s.legSort;
  const setLegSort = s.setLegSort;
  const handleLegSort = (key: string) => {
    setLegSort(legSort.key === key
      ? { key, dir: legSort.dir === 'asc' ? 'desc' : legSort.dir === 'desc' ? '' : 'asc' }
      : { key, dir: 'asc' });
  };

  React.useEffect(() => {
    if (!settingsOpen) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (target?.closest('[data-signal-table-settings]')) return;
      setSettingsOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [settingsOpen]);
  // The store persists it now; the standalone `kite_st_view_layout` key is only
  // read once, by the v7 migration, so an existing choice survives the move.

  // The signal table can still turn the engine back on from its dedicated off state.
  // All other engine configuration now lives under Connect → Engine.
  const patch = (values: Partial<EngineConfigModel>, message?: string, rescan = false) => {
    if (!cfg) return;
    setCfg.mutate(values, {
      onSuccess: () => {
        if (message) notifyOrder({ kind: 'info', title: 'Settings updated', message });
        if (rescan) rescanSupertrend();
      },
    });
  };
  // While a scan is in progress the backend flushes results progressively, so a
  // raw poll can momentarily return fewer rows than before — making rows blink out
  // and reappear. To keep the table stable, we remember the last completed-scan row
  // set and, during scanning, MERGE it with the freshly-arriving rows (fresh wins on
  // key collision). Rows therefore only get added/updated mid-scan, never vanish.
  // When the scan finishes, the fresh set becomes the new baseline.
  // `source` is part of the identity. In "both" mode the same contract can fire
  // once from the underlying's chart and once from its own premium chart; those
  // are two different setups with different entries. Keying without the source
  // let the later one silently overwrite the earlier in this merge map, so a
  // real signal could vanish — and the two were indistinguishable on screen.
  const rowKey = (r: EngineSignalRow) => `${r.source}:${r.token}:${r.option_type}:${r.timestamp_ms}`;
  const lastStableRows = React.useRef<EngineSignalRow[]>([]);
  const rawRows = React.useMemo(() => (signals?.rows ?? []).filter((row) => row.source !== 'navigator'), [signals?.rows]);
  const isScanning = signals?.scanning ?? false;
  const rows = React.useMemo(() => {
    if (!isScanning) {
      lastStableRows.current = rawRows;
      return rawRows;
    }
    const merged = new Map<string, EngineSignalRow>();
    for (const r of lastStableRows.current) merged.set(rowKey(r), r);
    for (const r of rawRows) merged.set(rowKey(r), r); // fresh overrides stale
    return Array.from(merged.values());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawRows, isScanning]);
  const [todayOnly, setTodayOnly] = React.useState<boolean>(() => readTableFlag('kite_st_today_only', false));
  const changeTodayOnly = (next: boolean) => {
    setTodayOnly(next);
    saveTableFlag('kite_st_today_only', next);
  };

  // Modes filter: SuperTrend trades one mode, and the board also lists the other modes'
  // signals (view-only), so filtering shows exactly the modes the user picks.
  const [stModesRaw, setStModesRaw] = useSavedSelection<string>('supertrend.board.modes', '');
  const stModes = React.useMemo(() => (stModesRaw ? stModesRaw.split(',') : []) as TradeMode[], [stModesRaw]);
  const stMode = normalizeMode(cfg?.horizon_mode);
  const filteredRows = React.useMemo(() => {
    // Mark legs a newer same-day signal replaced, so only the newest plan for a contract is buyable.
    const replaced = supersededLegKeys(rows);
    let result = replaced.size ? rows.map((r) => (r.legs ?? []).some((l: any) => replaced.has(`${r.timestamp_ms}|${l.option_symbol}`))
      ? { ...r, legs: r.legs.map((l: any) => replaced.has(`${r.timestamp_ms}|${l.option_symbol}`) ? { ...l, replaced: true } : l) }
      : r) : [...rows];
    if (stModes.length) result = result.filter((r) => stModes.includes((normalizeMode(r.horizon_mode) ?? stMode) as TradeMode));
    if (signalMode === 'navigator') {
      result = result.filter((r) => r.navigator != null);
    } else if (signalMode === 'common') {
      // "Common" means both systems agree — a row Navigator originated on
      // its own (no SuperTrend trigger at all) can't structurally satisfy
      // that, regardless of its own status.
      result = result.filter((r) => r.source !== 'navigator' && r.navigator != null && (r.navigator.status === 'CONFIRMED' || r.navigator.status === 'HIGH_CONVICTION'));
    } else if (signalMode === 'supertrend') {
      // "SuperTrend" means "what the board looks like with no Navigator at
      // all" — a Navigator-originated row has no real triple-ST basis behind
      // it, so it's excluded here rather than shown as a badge-less phantom.
      result = result.filter((r) => r.source !== 'navigator');
    }
    // 'combined' keeps every row (SuperTrend setups AND Navigator-originated
    // ones) — it differs from the others only in whether/how badges render
    // (see SignalCard).
    // Across the workspace, load today's signals for every strategy
    // before rendering SuperTrend's older rows, regardless of stored filters.
    if (todayOnly || (!priority.olderReady && !isHistoricalSim)) {
      const todayKey = sessionDayKey(effectiveNowMs);
      result = result.filter((r) => {
        const rawTs = (r as any).timestamp_ms ?? (r as any).timestamp ?? (r as any).time ?? (r as any).atMs;
        const day = sessionDayKey(rawTs);
        // Open/active signals are held exposure, not older history. Keep
        // them visible for the operator even while the history queue waits.
        return day === todayKey || (!todayOnly && !priority.olderReady && r.is_active);
      });
    }
    if (query.trim()) {
      const qLower = query.toLowerCase();
      result = result.filter(r => {
        if (r.underlying.toLowerCase().includes(qLower)) return true;
        if (r.exchange.toLowerCase().includes(qLower)) return true;
        if (r.legs.some(l => l.option_symbol.toLowerCase().includes(qLower))) return true;
        return false;
      });
    }
    return result;
  }, [rows, query, signalMode, todayOnly, effectiveNowMs, stModes, stMode, priority.olderReady, isHistoricalSim]);
  const showSignalPremiumColumns = React.useMemo(
    () => cfg?.scan_source !== 'spot' || filteredRows.some(hasPremiumSnapshot),
    [cfg?.scan_source, filteredRows],
  );

  // The engine keeps EVERY still-running entry transition, so one continuing trend on
  // one instrument can occupy several rows (NIFTY BANK long on 27, 29 and 30 Jul, all
  // "running"). Read as three independent setups that is three trades at three very
  // different entry prices; it is really one trend re-arming, and auto-exec's
  // one-position guard only ever takes the first. Mark the later ones so the board says
  // which entry is the original.
  //
  // Computed over `rows`, NOT `filteredRows`: whether an entry is the original is a
  // fact about the trend, not about what the current lens happens to show. Reading the
  // filtered set meant a lens that hid the first entry promoted the second to
  // "original", so a re-arm at a much worse price presented as an independent new
  // setup — the exact misreading this badge exists to prevent.
  const originalEntryMs = React.useMemo(() => {
    const earliest = new Map<string, number>();
    for (const r of rows) {
      if (!r.is_active) continue;  // ended rows are history, not a competing entry
      const key = `${r.underlying}|${r.direction}|${r.source ?? 'spot'}`;
      const prev = earliest.get(key);
      if (prev == null || r.timestamp_ms < prev) earliest.set(key, r.timestamp_ms);
    }
    return earliest;
  }, [rows]);

  // Live quotes are needed BEFORE bucketing so a position that has exited between scans
  // (live premium through its stop) drops out of "Active now" instead of lingering there
  // showing a "trend ended" badge — the same reconciliation the cards use.
  const optionSymbols = React.useMemo(() => {
    const syms = new Set<string>();
    filteredRows.forEach((r) => {
      const exch = (r.underlying === 'SENSEX' || r.underlying === 'BANKEX') ? 'BSE' : 'NSE';
      let sym = r.underlying;
      if (sym === 'NIFTY') sym = 'NIFTY 50';
      if (sym === 'BANKNIFTY') sym = 'NIFTY BANK';
      if (sym === 'FINNIFTY') sym = 'NIFTY FIN SERVICE';
      if (sym === 'MIDCPNIFTY') sym = 'NIFTY MID SELECT';
      syms.add(`${exch}:${sym}`);

      r.legs.forEach((l) => syms.add(`${r.exchange}:${l.option_symbol}`));
    });
    return Array.from(syms);
  }, [filteredRows]);

  const { data: quotes } = useKiteQuote(isHistoricalSim ? [] : optionSymbols, optionSymbols.length > 0 && !isHistoricalSim);

  // What the user has toggled this session, NOT what is collapsed. The default
  // is a rule (see isDayExpandedByDefault), and storing the collapsed set
  // instead meant the default could only ever be "everything open" — which is
  // what this was, so a week of ended setups all arrived expanded.
  const [userToggledGroups, setUserToggledGroups] = React.useState<Map<string, boolean>>(
    () => new Map(),
  );
  const [showEnded, setShowEnded] = React.useState<boolean>(() => readTableFlag('kite_st_show_ended', true));
  const [bestOnly, setBestOnly] = React.useState<boolean>(() => readTableFlag('kite_st_best_only', true));
  const changeShowEnded = (next: boolean) => {
    setShowEnded(next);
    saveTableFlag('kite_st_show_ended', next);
  };
  const changeBestOnly = (next: boolean) => {
    setBestOnly(next);
    saveTableFlag('kite_st_best_only', next);
  };
  const toggleGroup = (label: string, expandedNow: boolean) => {
    setUserToggledGroups(prev => {
      const next = new Map(prev);
      next.set(label, !expandedNow);
      return next;
    });
  };

  const groupedRows = React.useMemo(() => {
    const buckets: { label: string; rows: typeof filteredRows; active?: boolean }[] = [];

    // The chosen sort has to be applied HERE. Sorting `filteredRows` upstream
    // did nothing, because this memo re-sorted by timestamp and the "Active
    // now" bucket re-sorted alphabetically straight afterwards — which is why
    // the search bar's sort buttons appeared to do nothing on this board even
    // once they were reading the right value.
    const chgPctOf = (r: EngineSignalRow): number => {
      const exch = r.exchange || 'NSE';
      let sym = r.underlying;
      if (sym === 'MIDCPNIFTY') sym = 'NIFTY MID SELECT';
      const q = quotes?.[`${exch}:${sym}`];
      const base = q?.ohlc?.close || q?.net_change;
      if (q && base) return ((q.last_price - base) / base) * 100;
      return Number.NEGATIVE_INFINITY;  // unknown sorts last, never in the middle
    };
    const userSort = (a: EngineSignalRow, b: EngineSignalRow): number | null => {
      if (s.sortBy === 'A-Z') return a.underlying.localeCompare(b.underlying);
      if (s.sortBy === 'EXCH') return a.exchange.localeCompare(b.exchange);
      if (s.sortBy === 'LTP') return b.spot - a.spot;
      if (s.sortBy === '%') return chgPctOf(b) - chgPctOf(a);
      return null;  // 'Custom' — keep each bucket's own natural order
    };
    const applyUserSort = (list: typeof filteredRows) => {
      const sorted = [...list].sort((a, b) => {
        if (s.sortBy === 'Custom') {
          const aIdx = INDEX_NAMES.has(a.underlying) ? 1 : 0;
          const bIdx = INDEX_NAMES.has(b.underlying) ? 1 : 0;
          if (aIdx !== bIdx) return bIdx - aIdx; // indices first
          const cmp = a.underlying.localeCompare(b.underlying);
          if (cmp !== 0) return cmp;
          return (b.timestamp_ms || 0) - (a.timestamp_ms || 0);
        }
        return userSort(a, b) ?? 0;
      });
      return sorted;
    };

    const sorted = [...filteredRows].sort((a, b) => b.timestamp_ms - a.timestamp_ms);

    // Currently-running trades (SuperTrend still aligned on the latest bar) surface at
    // the TOP regardless of when they entered. Otherwise a live trade that entered a few
    // days ago hides under "Last week" and the list reads empty even though there's an
    // active signal. The date buckets below are the history log of entries whose trend
    // has since ended.
    const active = sorted.filter((r) => rowIsRunning(r, quotes));
    const history = sorted.filter((r) => !rowIsRunning(r, quotes));
    // Indices first in "Active now", then stocks alphabetically.
    const INDEX_NAMES = new Set([
      ...INDEX_OPTS.map(o => o.name),
      ...INDEX_OPTS.map(o => o.label),
      'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY MID SELECT', 'BANKEX', 'SENSEX'
    ]);
    const sortedActive = [...active].sort((a, b) => {
      const aIdx = INDEX_NAMES.has(a.underlying) ? 1 : 0;
      const bIdx = INDEX_NAMES.has(b.underlying) ? 1 : 0;
      if (aIdx !== bIdx) return bIdx - aIdx; // indices first
      const cmp = a.underlying.localeCompare(b.underlying);
      if (cmp !== 0) return cmp;
      return (b.timestamp_ms || 0) - (a.timestamp_ms || 0);
    });
    const nowMs = effectiveNowMs;
    const todayKey = sessionDayKey(nowMs);
    const realTodayKey = sessionDayKey(Date.now());
    const yesterdayKey = shiftSessionDay(todayKey, -1);
    const isHistorical = simNowMs != null && todayKey !== realTodayKey;
    const todayLabel = isHistorical ? formatSessionDay(todayKey) : 'Today';
    const yesterdayLabel = isHistorical ? formatSessionDay(yesterdayKey) : 'Yesterday';

    /* Still-running signals keep their place at the TOP — a live trade must not
       hide under an old date — but they are titled by the session they actually
       entered on. The single "Active now" bucket claimed the present tense for
       everything in it, so on a closed Saturday it read "Active now" over
       entries from 31 Aug and 3 Sept. Grouping by day keeps the hoist and drops
       the false claim.

       Rows from today or yesterday merge into those buckets (already the top
       two, so nothing moves); older ones get a dated bucket of their own,
       marked active, above the general history. */
    const activeByDay = new Map<string, typeof filteredRows>();
    const activeToday: typeof filteredRows = [];
    const activeYesterday: typeof filteredRows = [];
    for (const r of sortedActive) {
      const day = sessionDayKey(parseTimestampMs(r.timestamp_ms));
      if (day === todayKey || day === 'unknown') activeToday.push(r);
      else if (day === yesterdayKey) activeYesterday.push(r);
      else {
        const bucket = activeByDay.get(day) ?? [];
        bucket.push(r);
        activeByDay.set(day, bucket);
      }
    }
    // Newest dated group first, so the most recent live entry sits highest.
    for (const day of [...activeByDay.keys()].sort().reverse()) {
      buckets.push({
        label: `${formatSessionDay(day)} · active`,
        rows: applyUserSort(activeByDay.get(day)!),
        active: true,
      });
    }

    const groups: Record<string, typeof filteredRows> = {
      [todayLabel]: [...activeToday], [yesterdayLabel]: [...activeYesterday], Older: [],
    };
    for (const r of history) {
      const rawTs = parseTimestampMs(
        (r as any).timestamp_ms ?? (r as any).timestamp ?? (r as any).time ?? (r as any).atMs ?? (r as any).created_at ?? (r as any).session_date
      );
      const day = sessionDayKey(rawTs);
      if (day === todayKey || day === 'unknown') groups[todayLabel].push(r);
      else if (day === yesterdayKey) groups[yesterdayLabel].push(r);
      else groups.Older.push(r);
    }
    const dayBuckets: { label: string; rows: typeof filteredRows; active?: boolean }[] = [];
    for (const label of [todayLabel, yesterdayLabel, 'Older'] as const) {
      if (groups[label]?.length) {
        // `active` drives both the section styling and the `showEnded` filter
        // below, so a day bucket holding a running signal has to carry it.
        const hasActive = label === todayLabel ? activeToday.length > 0
          : label === yesterdayLabel ? activeYesterday.length > 0
          : false;
        const dedupeStockRows = (rows: typeof filteredRows) => {
          // Collapse duplicate signals only when they are truly for the same setup:
          // same underlying, same scan source, same direction — CE and PE are
          // independent positions and must NOT be merged into one. Key on
          // (underlying, source, direction) so a BHARTIARTL CE and PE remain separate.
          // Live-scanner vs simulation rows are distinguished by the backend's merge
          // logic (Fix 2) which gives simulation rows different timestamp_ms values
          // so the `rowKey` used for mid-scan merging already differentiates them.
          const byStock = new Map<string, EngineSignalRow>();
          for (const r of rows) {
            const key = `${r.underlying}|${r.source ?? 'spot'}|${r.direction ?? ''}`;
            const existing = byStock.get(key);
            if (!existing) {
              byStock.set(key, r);
            } else {
              const curLive = rowIsRunning(r, quotes);
              const exLive = rowIsRunning(existing, quotes);
              if (curLive && !exLive) {
                byStock.set(key, r);
              } else if (!curLive && exLive) {
                // keep existing live
              } else if ((r.timestamp_ms || 0) > (existing.timestamp_ms || 0)) {
                byStock.set(key, r);
              }
            }
          }
          return Array.from(byStock.values());
        };
        const rawBucketRows = (!showEnded && hasActive)
          ? groups[label].filter(r => rowIsRunning(r, quotes))
          : groups[label];
        const bucketRows = dedupeStockRows(rawBucketRows);
        if (bucketRows.length > 0) {
          dayBuckets.push({ label, rows: applyUserSort(bucketRows), active: hasActive });
        }
      }
    }
    // Today and Yesterday lead; the dated active groups sit between them and
    // the rest of the history.
    buckets.unshift(...dayBuckets.filter(b => b.label === todayLabel || b.label === yesterdayLabel));
    buckets.push(...dayBuckets.filter(b => b.label === 'Older'));
    if (!showEnded) return buckets.filter(b => b.active);
    return buckets;
  }, [filteredRows, showEnded, quotes, s.sortBy, s.chgType, simNowMs, effectiveNowMs]);
  const scanning = signals?.scanning;
  // The Navigator/Common lenses can legitimately show nothing even while
  // SuperTrend has live setups — Navigator may be disabled, still warming
  // up, or simply not agreeing with any of them yet. Say so explicitly
  // instead of falling through to the generic "hidden by table filters"
  // copy, which implies a search/showEnded filter is the cause.
  const engineEnabled = cfg?.engine_enabled === true;
  const supertrendLensBlocked = !engineEnabled && groupedRows.length === 0;

  const hiddenRecentCount = !isScanning && !supertrendLensBlocked && rows.length > 0 && groupedRows.length === 0
    ? rows.length
    : 0;
  const revealRecentSignals = () => {
    setQuery('');
    changeShowEnded(true);
    changeTodayOnly(false);
    // Drop the session's manual toggles so the default rule applies again —
    // which opens the newest band. Clearing a collapsed SET used to expand
    // every band at once, and this button exists to undo filters, not to
    // override the grouping.
    setUserToggledGroups(new Map());
  };

  const liveCount = rows.filter((r) => rowIsRunning(r, quotes)).length;

  // Publish the running count to the Kite footer (rendered in a different tree).
  // MUST be before the early-return below (hook count consistency) — cfg.engine_enabled
  // can flip while this component stays mounted, so every hook must run unconditionally.
  const setLiveCount = useLiveSignalCount((s) => s.setCount);
  React.useEffect(() => { setLiveCount(liveCount); }, [liveCount, setLiveCount]);
  React.useEffect(() => () => setLiveCount(0), [setLiveCount]);

  // ── Engine master gate ──────────────────────────────────────────────────────
  if (cfg && !cfg.engine_enabled && rows.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0,
                    background: k.bg, fontFamily: k.fontFamily }}>
        {/* minimal header — same chrome as the live pane */}
        <div style={{ padding: '12px 16px 8px', borderBottom: `1px solid ${k.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <EngineMark />
            <span style={{ fontSize: 14, color: k.text }}>Sterling Kite Engine</span>
            <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.4, color: k.dim,
                           border: `1px solid ${k.border}`, borderRadius: 4, padding: '1px 5px' }}>1H</span>
          </div>
        </div>
        {/* off-state body */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
                      justifyContent: 'center', gap: 20, padding: 32 }}>
          <div style={{ width: 52, height: 52, borderRadius: 26, background: k.border,
                        display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={k.dim}
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
            </svg>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: k.text, marginBottom: 6 }}>
              Engine is off
            </div>
            <div style={{ fontSize: 12, color: k.dim, lineHeight: 1.6, maxWidth: 260 }}>
              SuperTrend scanning is disabled. Manual trading, market watch,
              and existing position management remain available.
            </div>
          </div>
          <button
            onClick={() => patch({ engine_enabled: true }, 'Sterling Kite Engine enabled', true)}
            disabled={setCfg.isPending}
            style={{ padding: '10px 28px', borderRadius: 8, border: 'none', cursor: 'pointer',
                     background: k.green, color: 'var(--k-bg)', fontSize: 13, fontWeight: 700,
                     opacity: setCfg.isPending ? 0.6 : 1, transition: 'opacity 0.15s' }}>
            Enable Engine
          </button>
        </div>
      </div>
    );
  }

  // The engine's own controls — timeframe, SOURCE, EXIT and the VIEW lens.
  // They had a row of their own above the table; they now sit on the search row
  // between the search box and COLUMNS, which is a row that already existed.
  const engineControls = (
    <>
                <span title={universeTip(cfg)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  <EngineMark />
                  {/* Same chip family as the rest of the row: 22px, radius 4.
                      It was radius 3 with its own padding, which is the kind of
                      one-pixel difference that reads as sloppiness rather than
                      as a distinction. */}
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 7px',
                    fontSize: 9, fontWeight: 700, letterSpacing: '.05em', color: k.dim,
                    border: `1px solid ${k.border}`, borderRadius: 4, whiteSpace: 'nowrap',
                  }}>1H</span>
                </span>

                {/* SuperTrend's release controls. */}
                {cfg && (
                  <>
                      <InlineDropdown
                        label="SOURCE"
                        scope="server"
                        value={cfg.scan_source}
                        options={SCAN_SOURCE_OPTS}
                        tone={k.orange}
                        title="SOURCE — Which chart SuperTrend reads a signal from. Saved on the server."
                        onChange={(next) => patch(
                          { scan_source: next },
                          `Signal source changed to ${SCAN_SOURCE_OPTS.find((option) => option.value === next)?.label}`,
                          needsRescan('scan_source'),
                        )}
                      />
                      <InlineDropdown
                        label="EXIT"
                        scope="server"
                        value={cfg.exit_mode ?? 'one_red'}
                        options={EXIT_MODE_OPTS}
                        tone={k.blue}
                        title="EXIT — How many SuperTrend lines must turn red to close a trade. Saved on the server and applied to every live SuperTrend position."
                        onChange={(next) => patch(
                          { exit_mode: next },
                          `Exit rule changed to ${EXIT_MODE_OPTS.find((option) => option.value === next)?.label}`,
                          needsRescan('exit_mode'),
                        )}
                      />
                  </>
                )}
    </>
  );

  // Rescan and table settings. Portalled into the pane title bar beside
  // minimize; renders here if no title bar exists.
  // Rescan and the table settings moved to the engine-tab shell.
  //
  // They were rendered here, which meant they existed on the SuperTrend tab
  // and nowhere else — yet rescan already scans all five engines and every
  // setting in that drawer lives in the shared store. Two controls common to
  // the whole dock were only reachable from one fifth of it.
  //
  // See AdaptiveEdgeRightSidebar, which renders regardless of engine.

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: k.bg, fontFamily: k.fontFamily }}>
      {/*
        The engine's own controls, on the shared toolbar grammar.

        The old header opened with "Sterling Kite Engine", which stopped being
        true the moment there were four boards — and the engine tabs directly
        above already name the one you are looking at, so the words were both
        wrong and redundant. What is left is what only this header can say: the
        timeframe it reads, the rules it applies, and the actions it offers.

        The divider is load-bearing. Left of it, SOURCE and EXIT are saved on
        the server and change what is scanned and how live trades exit. Right
        of it, VIEW is a local lens that changes nothing. They looked identical
        before, which is a dangerous thing for a control that closes positions.
      */}
      <div style={{ borderBottom: `1px solid ${k.border}`, flexShrink: 0 }}>

        {/* The scan progress bar was here, above the search row. It spent a full
            band restating something the rescan button already shows by spinning,
            and the scan label is in that button's tooltip. On a dock this dense
            a row of chrome has to earn its height. */}
      </div>
      {/* Not gated on `rows.length` any more. This row now carries the engine
          controls and the rescan button, and those are most needed when the table
          is EMPTY — gating them on having rows meant the one press that could
          fill it disappeared exactly when nothing was there. Only the search box
          itself is pointless with no rows, so only it is gated. */}
      {/* Provenance, above everything. While a replay holds the session view the
          backend merges its recorded rows into this live table, so the table has
          to say so — a live board that silently contains simulated rows is the
          one failure this surface must not have. `feed_mode` comes from the
          engine; it is never inferred from the rows. */}
      {signals?.feed_mode && signals.feed_mode !== 'live' && (
        <div
          data-testid="kite-replay-provenance"
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '5px 10px', flexShrink: 0,
            fontSize: 11, fontWeight: 600, letterSpacing: '0.02em',
            color: k.amber,
            background: `color-mix(in srgb, ${k.amber} 12%, transparent)`,
            borderBottom: `1px solid color-mix(in srgb, ${k.amber} 35%, transparent)`,
          }}
        >
          <span>
            {signals.feed_mode === 'replay'
              ? 'MARKET REPLAY — these rows are a recording, not the live market.'
              : 'FINISHED REPLAY — these rows are a recording. Clear the replay results to return to live.'}
          </span>
        </div>
      )}
      {!settingsOpen && (
        <div style={{ position: 'sticky', top: 0, zIndex: 10, background: k.bg }}>
          <div className="signal-mobile-controls strategy-inline-controls" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderBottom: `1px solid ${k.border}` }}>
            {/* After search, before COLUMNS. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, flexWrap: 'wrap' }}>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, flexWrap: 'wrap' }}>
              <ViewFiltersDisclosure>
              {/* The columns this table actually renders, in the order it
                  renders them — not the six abstract visibility groups the old
                  gear exposed. `premium` is filtered out rather than offered:
                  Entry, SL, TSL and Target need a scan source that produces
                  premiums, so on a spot scan they are unavailable, not hidden. */}
              <ColumnsMenu
                items={[
                  ...s.signalLeftColumnOrder
                    .map((key) => SIGNAL_LEFT_COLUMNS[key])
                    .filter((col) => col && col.key !== 'dir' && signalColCapable(col.visibleWhen, showSignalPremiumColumns)),
                  ...s.signalRightColumnOrder
                    .map((key) => SIGNAL_RIGHT_COLUMNS[key])
                    .filter((col) => col && col.key !== 'dir' && signalColCapable(col.visibleWhen, showSignalPremiumColumns)),
                ].map((col) => ({
                  id: col.key,
                  // `dir` is an unlabelled arrow in the header, so the menu has to
                  // name it — an unnamed checkbox is not a choice. "arrow"
                  // because the entry below governs the colours, and two items
                  // both called Direction would be a coin toss.
                  label: col.label || 'Direction arrow',
                  on: !s.hiddenSignalCols.includes(col.key),
                  toggle: () => s.toggleSignalCol(col.key),
                })).concat([{
                  // Not a column — a display option, and the only one that was
                  // lost when this table stopped offering the watchlist's gear.
                  // It tints LTP and Chg. green or red by the move, which the
                  // table has always honoured; there was simply no longer
                  // anywhere to switch it. The COLUMNS menu is where this table
                  // keeps its other per-column display choices, so it goes here.
                  id: 'showPriceDirection',
                  label: 'Direction colours',
                  on: s.showPriceDirection,
                  toggle: () => s.toggleShow('showPriceDirection'),
                }])}
                onShowAll={() => {
                  s.showAllSignalCols();
                  // "Show all" that left the colours off would be a lie.
                  if (!s.showPriceDirection) s.toggleShow('showPriceDirection');
                }}
              />
              <ModeFilter modes={stModes} onChange={(next) => setStModesRaw(next.join(','))} />
              <FilterToggle
                on={bestOnly}
                label="BEST PICK"
                hint="Show only the nearest-the-money leg of each underlying — the one whose premium tracks the thesis most directly. A local filter; it never changes what is scanned."
                onChange={() => changeBestOnly(!bestOnly)}
              />
              <FilterToggle
                on={todayOnly}
                label={isHistoricalSim ? 'REPLAY DATE ONLY' : 'TODAY ONLY'}
                hint={isHistoricalSim ? `Show only signals generated on the replay date (${currentSimDayLabel}).` : 'Show only signals generated today. Hides historical setups from yesterday and older sessions.'}
                onChange={() => changeTodayOnly(!todayOnly)}
              />
              <FilterToggle
                on={showEnded}
                label="CLOSED"
                hint="Include closed positions. They are kept for the record and are not calls to action."
                onChange={() => changeShowEnded(!showEnded)}
              />
              </ViewFiltersDisclosure>
              <StrategyBoardFooter name="SuperTrend">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '6px 12px' }}>
            {engineControls}
          </div>
        </StrategyBoardFooter>
            </div>
          </div>
          {/* The shared renderer draws its OWN heading strip, so this one must not
              also render — leaving both in place put two of every column label
              on screen, which is how "Found multiple elements with the text:
              Entry (Δpts)" showed up when the default was trial-flipped. */}

        </div>
      )}
      <style>{`
        .st-parent-row {
          position: relative;
          background: ${k.bg};
        }
        /* Row hover, focus and active now come from styles/globals.css, which
           states them once for this table's rows and the shared board's. This
           table used to declare a bare :hover here with no transition, focus or
           active state, so its highlight snapped on and keyboard focus showed
           nothing -- on one table only. */

        .col-drag-over {
          box-shadow: inset 2px 0 0 0 ${k.blue};
        }

        .st-leg-row {
          position: relative;
          display: flex;
          align-items: center;
          /* Geometry from ROW_METRICS, the spec the shared board renders against,
             rather than the literals this table was built with. A minimum height
             rather than a fixed one: a fixed height clips a cell that wraps, and
             the shared row has always used a minimum. No backticks in here - this
             block is a template literal and one would close it. */
          gap: ${ROW_METRICS.gap}px;
          min-height: ${ROW_METRICS.legHeight}px;
          /* The shared row sets 400 on a leg and 600 on a parent. Stated here so
             the instrument cell inherits it rather than depending on a browser
             default -- and so nothing has to set a weight per cell, which is how
             this table ended up bold in the first place. */
          font-weight: 400;
          /* Indented under the parent, like the shared board's legs. The
             recessed shade groups them, but only the indent ties them to the
             row above once that row has scrolled away. */
          padding: 0 16px 0 ${16 + LEG_INDENT}px;
          box-sizing: border-box;
          /* The shade above separates one row from the next, so the per-row line
             that this table used to draw is now redundant - and drawing both
             gives a heavier grid than the shared board. Kept as a transparent
             edge rather than deleted so the row keeps its box height. */
          border-bottom: 1px solid transparent;
          /* Reserved, not decorative: the shared board turns this gutter blue on
             the open row, and a border that appears later would shift every cell
             3px sideways. Holding the space means it never does. */
          border-left: 3px solid transparent;
        }
        /* Sideways scrolling is opt-in per the board's Behaviour setting. Off, the
           row clips instead, and the operator hides columns to fit -- which is
           what the shared board has always done. */
        .st-row-scroll {
          overflow-x: auto;
          overflow-y: hidden;
          scrollbar-width: none;
        }
        .st-row-scroll::-webkit-scrollbar { display: none; }
        .st-leg-row:not(.st-row-scroll) { overflow: hidden; }
        .st-header-row { scrollbar-width: none; }
        .st-header-row::-webkit-scrollbar { display: none; }
        /* The heading's hover colour comes from the sb-head rule in globals.css
           now -- this element carries that class. Only the sort glyph's fade
           stays local, because this table's glyph is not the aria-hidden span
           the shared rule targets. NO BACKTICKS IN HERE: this block is a
           template literal and one closes it. */
        .sort-icon { opacity: 0; color: var(--k-dim); display: flex; flex-direction: column; gap: 2px; align-items: center; transition: opacity 0.2s; }
        .sort-header-div:hover .sort-icon { opacity: 0.5; }
        .sort-icon.active { opacity: 1 !important; color: var(--k-text); }
        .st-actions-persistent {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .st-actions-more-persistent {
          display: flex;
          align-items: center;
        }
        .st-prices {
          display: flex;
          align-items: center;
          gap: 2px;
          flex-shrink: 0;
          justify-content: flex-end;
        }
        .st-spin { animation: st-spin .8s linear infinite; transform-origin: 50% 50%; }
        @keyframes st-spin { to { transform: rotate(360deg); } }
        .st-pulse { animation: st-pulse 1.5s ease-in-out infinite; }
        @keyframes st-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
        .st-drawer { transition: grid-template-rows .22s ease; }
        .st-signal-in { animation: none; }
        @keyframes st-signal-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
        @media (prefers-reduced-motion: reduce) {
          .st-spin, .st-pulse, .st-drawer, .st-signal-in { animation: none !important; transition: none !important; }
        }
      `}</style>

      {/* Table-only preferences. Trading logic is configured in Connect → Engine. */}
      <div data-signal-table-settings className="st-drawer" style={{ display: 'grid', gridTemplateRows: settingsOpen ? '1fr' : '0fr' }}>
        <div style={{ overflow: 'hidden' }}>
          {settingsOpen && (
            <SignalTableSettingsPanel />
          )}
        </div>
      </div>
      {/* Signal list */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {hiddenRecentCount > 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            <div>
              {hiddenRecentCount} recent setup{hiddenRecentCount === 1 ? ' is' : 's are'} hidden by the current table filters.
            </div>
            <button
              type="button"
              onClick={revealRecentSignals}
              style={{ marginTop: 12, minHeight: 32, padding: '0 12px', border: `1px solid ${k.border}`, borderRadius: 6, background: 'var(--k-bg)', color: k.orange, fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              Show recent signals
            </button>
          </div>
        ) : supertrendLensBlocked ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            <div style={{ fontWeight: 700, color: k.text, fontSize: 12.5 }}>SuperTrend is off</div>
            <div style={{ marginTop: 6, maxWidth: 420, marginLeft: 'auto', marginRight: 'auto', lineHeight: 1.6 }}>
              This board shows only SuperTrend setups, and the engine is not scanning.
            </div>
            <div style={{ marginTop: 12, display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
              {cfg && (
                <button
                  type="button"
                  onClick={() => setCfg.mutate({ engine_enabled: true })}
                  disabled={setCfg.isPending}
                  style={{ minHeight: 32, padding: '0 14px', border: `1px solid ${k.blue}`, borderRadius: 6, background: setCfg.isPending ? 'var(--k-bg)' : k.blue, color: setCfg.isPending ? k.blue : 'var(--k-bg)', fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: setCfg.isPending ? 'wait' : 'pointer' }}
                >
                  {setCfg.isPending ? 'Turning on…' : 'Turn on SuperTrend'}
                </button>
              )}
            </div>
            {setCfg.isError && (
              <div style={{ marginTop: 8, fontSize: 11, color: k.red }}>Could not turn it on: {(setCfg.error as Error).message}</div>
            )}
          </div>
        ) : groupedRows.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            {signalsLoading ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                <KiteLoader size={26} />
                <span>Loading signal board…</span>
              </div>
            ) : scanning ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                <KiteLoader size={26} />
                <span>{`Scanning ${signals?.scanning_label || '…'}`}</span>
              </div>
            ) : signals?.market_open ? 'No active or recent setups on the board yet. The engine re-scans every ~5 min.' : `No recent signals on the board. Recent setups stay listed; the engine resumes when markets open (Mon–Fri 9:15 AM – 3:30 PM IST).`}
          </div>
        ) : (
          <SuperTrendSharedBoard
            mode={cfg?.horizon_mode}
            rows={filteredRows}
            quotes={quotes}
            originalEntryMs={originalEntryMs}
            onSelectSignal={onSelectSignal}
            onOpenChart={onOpenChart ? (symbol, tab, signalData) => onOpenChart(symbol, tab, cfg?.trail_target, signalData) : undefined}
            nowMs={effectiveNowMs}
            isHistoricalSim={isHistoricalSim}
            signalMode={signalMode}
          />
        )}

        {/* IN-TABLE SCANNING PROGRESS CARD (WHILE ROWS ALREADY LOADED) */}
        {groupedRows.length > 0 && scanning && (
          <div
            style={{
              margin: '12px 16px',
              padding: '10px 16px',
              background: 'var(--k-surface)',
              border: `1px dashed ${k.blue}60`,
              borderRadius: 4,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 10,
              fontFamily: k.fontFamily,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, position: 'relative', flexShrink: 0 }}>
                <span
                  style={{
                    position: 'absolute',
                    width: 18,
                    height: 18,
                    borderRadius: '50%',
                    background: `${k.blue}20`,
                    animation: 'pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                  }}
                />
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: k.blue,
                  }}
                />
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: k.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span>Scanning in background…</span>
                  {signals?.scanning_label && (
                    <span style={{ fontSize: 11, color: k.blue, fontWeight: 500 }}>
                      ({signals.scanning_label})
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: k.dim, marginTop: 2 }}>
                  Scanning derivatives and multi-timeframe candles. Loaded setups above remain live and interactive.
                </div>
              </div>
            </div>
            <div style={{ fontSize: 11, color: k.dim, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
              {rows.length} {rows.length === 1 ? 'setup active' : 'setups active'}
            </div>
          </div>
        )}
      </div>

    </div>
  );
}

export default SterlingKiteEnginePane;

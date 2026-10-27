import {useMobileBackLayer} from '../../../hooks/useMobileBackLayer';
import { layoutViewport, toLayoutPx } from '../../../styles/applyViewportScale';
/**
 * The view-filter row: search, then the toggles that have something to act on.
 *
 * Every control here is local. A filter that changed a trading rule would
 * belong on the other side of the toolbar's divider, and none of these do.
 *
 * A toggle whose filter is inert is not rendered at all rather than shown
 * disabled — "Ended" greyed out on a board with no ended rows is noise that
 * looks like a broken control.
 */
import React from 'react';
import { createPortal } from 'react-dom';
import { COLUMNS, type ColumnId } from './SignalBoard';
import { k, tint } from '../../../styles/kiteUI';
import type { BoardView } from './useBoardView';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { MODE_LABEL, TRADE_MODES, sessionDayKey, type TradeMode } from './boardTypes';
import { ViewFiltersDisclosure } from './ViewFiltersDisclosure';

/**
 * A local view filter.
 *
 * Exported because SuperTrend's board has its own filter row and had its own
 * differently-shaped toggles. Two controls that do the same job should not look
 * like two different jobs.
 */
export function FilterToggle({ on, label, hint, onChange }: {
  on: boolean; label: string; hint: string; onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={hint}
      onClick={onChange}
      className="sb-tool"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, height: 22, padding: '0 7px',
        border: `1px solid ${on ? tint(k.blue, 45) : k.border}`, borderRadius: 4,
        background: on ? tint(k.blue, 10) : 'transparent',
        color: on ? k.blue : k.dim,
        fontFamily: 'inherit', fontSize: 9, fontWeight: 700, letterSpacing: '.05em',
        cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
      }}
    >
      <span aria-hidden style={{
        width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
        background: on ? k.blue : k.border,
      }} />
      {label}
    </button>
  );
}

/**
 * Which columns to show.
 *
 * Only offers columns the board could actually render — hiding a column no row
 * can fill is a control for nothing, and listing all fifteen on a board that
 * shows nine invites a user to switch on a column that then does not appear.
 *
 * The row identity symbol column is locked; all other columns (including Status) can be toggled.
 */
const LOCKED: readonly ColumnId[] = ['instrument'];

export interface ColumnChoice {
  id: string;
  label: string;
  on: boolean;
  toggle: () => void;
}

/**
 * The COLUMNS button and its menu, driven by a plain list of choices.
 *
 * Presentational on purpose. Two boards keep their visible columns in
 * completely different places — the shared board in a `BoardView`, SuperTrend in
 * the Kite settings store, keyed by its own row spec rather than by `ColumnId` —
 * and mapping one onto the other would be lossy in the direction that silently
 * drops a column. So the control takes choices and reports toggles, and each
 * caller keeps its own model.
 *
 * That is what let SuperTrend finally have this button. It had none: its column
 * toggles were buried in an unlabelled gear inside the search bar, next to BEST
 * LEG, in a panel that also carried watchlist-only settings like Change type.
 */
export function ColumnsMenu({ items: allItems, onShowAll, buttonText = 'Columns',
  menuLabel = 'Visible columns', buttonTitle = 'Choose which columns this table shows',
  showAllText = 'Show all columns' }: {
  items: readonly ColumnChoice[];
  onShowAll?: () => void;
  buttonText?: string;
  menuLabel?: string;
  buttonTitle?: string;
  showAllText?: string;
}) {
  const items = allItems.filter(item => item.id !== 'dir');
  const [open, setOpen] = React.useState(false);
  useMobileBackLayer(open,()=>setOpen(false),'signal-columns');
  const ref = React.useRef<HTMLDivElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = React.useState({ top: 0, right: 8, maxHeight: 240 });
  React.useEffect(() => {
    if (!open) return;
    const position = () => {
      const anchor = ref.current?.getBoundingClientRect();
      if (!anchor) return;
      const gap = 4;
      const viewport = layoutViewport();
      const bottom = toLayoutPx(anchor.bottom);
      const anchorTop = toLayoutPx(anchor.top);
      const below = viewport.height - bottom - gap;
      const above = anchorTop - gap;
      const height = Math.max(100, Math.min(320, Math.max(below, above) - 8));
      const top = below >= 130 || below >= above ? bottom + gap : Math.max(8, anchorTop - Math.min(320, above) - gap);
      setPlacement({ top, right: Math.max(8, Math.min(viewport.width - 188, viewport.width - toLayoutPx(anchor.right))),
        maxHeight: height });
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!ref.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!items.length) return null;
  const hiddenCount = items.filter((c) => !c.on).length;

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={hiddenCount ? `${buttonText}, ${hiddenCount} hidden` : buttonText}
        title={buttonTitle}
        onClick={() => setOpen((v) => !v)}
        className="sb-tool"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, height: 22, padding: '0 7px',
          border: `1px solid ${hiddenCount ? tint(k.blue, 45) : k.border}`, borderRadius: 4,
          background: hiddenCount ? tint(k.blue, 10) : 'transparent',
          color: hiddenCount ? k.blue : k.dim,
          fontFamily: 'inherit', fontSize: 9, fontWeight: 700, letterSpacing: '.05em', cursor: 'pointer',
        }}
      >
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden>
          <path d="M4 5h16M4 12h16M4 19h16" strokeLinecap="round" />
        </svg>
        {buttonText}{hiddenCount ? ` ${items.length - hiddenCount}/${items.length}` : ''}
      </button>

      {open && createPortal(
        <div
          ref={menuRef}
          role="group"
          aria-label={menuLabel}
          style={{
            position: 'fixed', top: placement.top, right: placement.right,
            zIndex: 2147483000, width: 180, boxSizing: 'border-box', maxHeight: placement.maxHeight,
            overflowY: 'auto', overscrollBehavior: 'contain',
            background: k.bg, border: '1px solid var(--k-border-strong)', borderRadius: 6,
            boxShadow: '0 10px 26px rgba(0, 0, 0, .26)', padding: 6,
          }}
        >
          {items.map((col) => (
            <label
              key={col.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 7, padding: '4px 5px', borderRadius: 3,
                fontSize: 10.5, color: k.text, cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={col.on}
                onChange={col.toggle}
                style={{ width: 13, height: 13, margin: 0, accentColor: k.orange }}
              />
              {col.label}
            </label>
          ))}
          {hiddenCount > 0 && onShowAll && (
            <button
              type="button"
              onClick={onShowAll}
              style={{
                width: '100%', marginTop: 4, padding: '4px 5px', border: 'none', borderTop: `1px solid ${k.border}`,
                background: 'transparent', color: k.blue, fontFamily: 'inherit', fontSize: 9.5, fontWeight: 600,
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              {showAllText}
            </button>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}

/** The shared board's columns, expressed as choices for {@link ColumnsMenu}. */
function ColumnPicker({ view, available, columnLabels }: {
  view: BoardView;
  available: readonly ColumnId[];
  columnLabels?: Partial<Record<ColumnId, string>>;
}) {
  const items = COLUMNS
    .filter((c) => available.includes(c.id) && !LOCKED.includes(c.id))
    .map((c) => ({
      id: c.id,
      label: columnLabels?.[c.id] ?? c.label,
      on: !view.hidden.has(c.id),
      toggle: () => view.toggleColumn(c.id),
    }));
  return <ColumnsMenu items={items} onShowAll={view.showAllColumns} />;
}

/** One mode filter for every strategy. Empty selection means all five modes. */
export function ModeFilter({ modes, onChange }: { modes: readonly TradeMode[]; onChange: (next: TradeMode[]) => void }) {
  const on = (m: TradeMode) => modes.length === 0 || modes.includes(m);
  return <ColumnsMenu buttonText={modes.length === 0 || modes.length === TRADE_MODES.length ? 'MODES' : `MODES ${modes.length}`}
    menuLabel="Trading modes shown (view filter only)" buttonTitle="Show only some trading modes. Does not change scanning or orders."
    showAllText="Show all modes"
    items={TRADE_MODES.map((m) => ({
      id: m as unknown as ColumnId, label: MODE_LABEL[m], on: on(m),
      toggle: () => {
        const current = modes.length === 0 ? [...TRADE_MODES] : [...modes];
        const next = current.includes(m) ? current.filter((x) => x !== m) : [...current, m];
        onChange(next.length === 0 || next.length === TRADE_MODES.length ? [] : next);
      },
    }))}
    onShowAll={() => onChange([])} />;
}

export function BoardFilters({ view, columns, columnLabels, children, hideSearch = false, alwaysChips = true, showModes = false }: {
  view: BoardView;
  /** The columns this board asks for, so the picker offers only those. */
  columns?: readonly ColumnId[];
  /** Per-engine heading names — ORB says Entry ₹, SuperTrend keeps Entry (Δpts). */
  columnLabels?: Partial<Record<ColumnId, string>>;
  children?: React.ReactNode;
  hideSearch?: boolean;
  /** Show Best leg / Today only / Ended on every board, even when they would change nothing. */
  alwaysChips?: boolean;
  /** Trading modes exist only for SuperTrend and Snapback; other strategies run their original settings. */
  showModes?: boolean;
}) {
  React.useEffect(() => { if (hideSearch && view.query) view.setQuery(''); }, [hideSearch, view.query, view.setQuery]);
  const { counts } = view;
  const simNowMs = useSimNowMs();
  const isHistoricalSim = simNowMs != null && sessionDayKey(simNowMs) !== sessionDayKey(Date.now());
  return (
    <div className="signal-mobile-controls strategy-inline-controls" style={{
      display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px',
      borderBottom: `1px solid ${k.border}`, background: k.bg, flexWrap: 'wrap',
    }}>
      {!hideSearch && <label style={{ flex: '1 1 130px', minWidth: 110, display: 'flex', alignItems: 'center', gap: 5 }}>
        <span className="sr-only" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
          Filter signals
        </span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke={k.dim} strokeWidth="2.4" aria-hidden style={{ flexShrink: 0 }}>
          <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.6-3.6" strokeLinecap="round" />
        </svg>
        <input
          value={view.query}
          onChange={(e) => view.setQuery(e.target.value)}
          placeholder="Symbol, contract or exchange"
          style={{
            flex: 1, minWidth: 0, height: 22, border: `1px solid ${k.border}`, borderRadius: 4,
            background: k.bg, color: k.text, fontFamily: 'inherit', fontSize: 10, padding: '0 6px',
          }}
        />
      </label>}

      <ViewFiltersDisclosure>
      {/* Columns first, as on SuperTrend's toolbar, so every strategy reads the same left to right. */}
      {columns && <ColumnPicker view={view} available={columns} columnLabels={columnLabels} />}
      {showModes && view.setModes && <ModeFilter modes={view.modes ?? []} onChange={view.setModes} />}
      {(view.offers.best || alwaysChips) && (
        <FilterToggle
          on={view.bestOnly}
          label="BEST PICK"
          hint="Show only the nearest-the-money leg of each underlying — the one whose premium tracks the thesis most directly. A local filter."
          onChange={() => view.setBestOnly(!view.bestOnly)}
        />
      )}
      {(view.offers.today || alwaysChips) && (
        <FilterToggle
          on={view.todayOnly}
          label={isHistoricalSim ? 'REPLAY DATE ONLY' : 'TODAY ONLY'}
          hint={isHistoricalSim ? 'Show only signals from the replay date.' : 'Show only signals generated today. Hides historical setups from yesterday and older sessions.'}
          onChange={() => view.setTodayOnly(!view.todayOnly)}
        />
      )}
      {(view.offers.ended || alwaysChips) && (
        <FilterToggle
          on={view.showEnded}
          label={`CLOSED ${counts.ended}`}
          hint="Include closed positions. They are kept for the record and are not calls to action."
          onChange={() => view.setShowEnded(!view.showEnded)}
        />
      )}

      </ViewFiltersDisclosure>
      {children}

      {counts.shown !== counts.total && (
        <span style={{ fontSize: 9, color: k.dim, whiteSpace: 'nowrap', marginLeft: 'auto' }}>
          {counts.shown} of {counts.total}
        </span>
      )}
    </div>
  );
}

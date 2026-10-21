import { useSignalMarkers, type Marker } from '../../store/useSignalMarkers';
import { BoardTicket } from './board/BoardTicket';
import { signalChartDataForPremiumLeg } from '../charts/signalMarkerLogic';
import type { SignalChartData } from '../../types/kiteEngine';
import React from 'react';
import { SignalBoard, DEFAULT_SORT, type ColumnId, type SortState } from './board/SignalBoard';
import { supertrendToBoard } from './board/supertrendAdapter';
import { markLegs, underlyingQuoteKey, sessionDayKey, type BoardSignal } from './board/boardTypes';
import {
  BOARD_COL_TO_SIGNAL, SIGNAL_COL_TO_BOARD, SIGNAL_LEFT_COLUMNS, SIGNAL_RIGHT_COLUMNS,
  signalColGroup, type SignalColKey,
} from './board/signalRowSpec';
import { useKiteSettings } from '../../store/useKiteSettings';
import { KiteActionButtons } from './KiteActionButtons';
import { useBoardRowActions } from './board/useBoardRowActions';
import { useSignalColumns } from './board/useSignalColumns';
import type { EngineSignalRow } from '../../types/kiteEngine';

/**
 * SuperTrend's signals, rendered by the shared board.
 *
 * This is the point of the last several commits. Four of the five engines have
 * always rendered through `SignalBoard`; SuperTrend kept a bespoke ~2,700-line
 * table, and every visual difference between the two — the heading scale, the
 * leg shade, the hover, the timestamp, the bold instrument — was a separate
 * thing to notice and fix. Rendering both through one component is what makes
 * that class of difference impossible rather than merely fixed.
 *
 * The three features that used to justify the second implementation are passed
 * in as capabilities, driven by the operator's own settings, so nothing is lost
 * by moving and nobody had to decide for them which ones to drop.
 */
export function SuperTrendSharedBoard({
  rows, quotes, originalEntryMs, onSelectSignal, onOpenChart, nowMs, signalMode, isHistoricalSim, mode,
}: {
  rows: readonly EngineSignalRow[];
  quotes?: Record<string, any>;
  originalEntryMs?: Map<string, number>;
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  /** Tab is narrowed to what the host accepts; widening it here only moves the error. */
  /** Signature narrowed to the host's, so a mismatch surfaces here not there. */
  onOpenChart?: (symbol: string, tab: 'chart', signalData?: SignalChartData) => void;
  nowMs: number;
  /** The lens, so the adapter can suppress a Navigator badge under 'supertrend'. */
  signalMode?: 'supertrend' | 'navigator' | 'combined' | 'common';
  isHistoricalSim?: boolean;
  /** SuperTrend's configured trading mode, shown on every row. */
  mode?: string | null;
}) {
  const s = useKiteSettings();
  const commonColumns = useSignalColumns();
  // The same builder every other board uses, so SuperTrend stops being the only
  // engine with order buttons by accident of where they were written.
  const rowActions = useBoardRowActions({
    onOpenChart: onOpenChart ? (key) => onOpenChart(key, 'chart') : undefined,
  });
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [sort, setSort] = React.useState<SortState>(DEFAULT_SORT);
  const [collapsed, setCollapsed] = React.useState<ReadonlySet<string>>(new Set());

  /**
   * The underlying's LIVE price, for the parent row.
   *
   * Without this the adapter falls back to `row.spot`, which is a scan-time
   * snapshot — so a parent row's price never moved between scans, and a
   * PREMIUM-source signal had no price at all, because that signal was read from
   * the option's own premium chart and carries no underlying spot. That is why
   * some rows showed a price and some did not, and why none of them updated.
   *
   * The quotes are already subscribed: the pane adds every signal's underlying
   * to its quote set alongside the option legs.
   */
  const spotOf = React.useCallback(
    (underlying: string) => {
      const q = quotes?.[underlyingQuoteKey(underlying)];
      const last = q?.last_price;
      return typeof last === 'number' && last > 0 ? last : null;
    },
    [quotes],
  );

  const stMode = mode ?? null;
  const signals = React.useMemo<BoardSignal[]>(
    () => supertrendToBoard(isHistoricalSim ? rows.map(row => ({ ...row, is_replay: true })) : rows, {
      quotes: isHistoricalSim ? undefined : quotes,
      originalEntryMs,
      spotOf,
      // The operator's close-vs-open choice, which this board only reports.
      chgBasis: s.chgType,
      signalMode,
      nowMs,
      mode: stMode,
    }),
    [rows, quotes, originalEntryMs, spotOf, s.chgType, signalMode, nowMs, isHistoricalSim, stMode],
  );

  const publishMarkers = useSignalMarkers(state => state.publish);
  const clearMarkers = useSignalMarkers(state => state.clear);
  React.useEffect(() => {
    const keys: string[] = [];
    signals.forEach((signal, index) => {
      const row = rows[index];
      const key = `${row.source ?? 'spot'}:${row.token}:${row.option_type}:${row.timestamp_ms}`;
      const entries: Record<string, Marker> = {};
      const comparisons = markLegs(signal.children ?? []);
      (signal.children ?? []).forEach(leg => {
        const marks = comparisons.get(leg.id);
        if (leg.instrument.quoteKey && marks?.size) entries[leg.instrument.quoteKey] = {
          rr: marks.has('bestRR'), delta: marks.has('bestDelta'),
        };
      });
      publishMarkers(key, entries); keys.push(key);
    });
    return () => keys.forEach(clearMarkers);
  }, [signals, rows, publishMarkers, clearMarkers]);

  const toggleGroup = React.useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const hasToday = React.useMemo(() => {
    const todayKey = sessionDayKey(nowMs);
    return rows.some((r) => sessionDayKey(r.timestamp_ms) === todayKey);
  }, [rows, nowMs]);

  return (
    <>
      <SignalBoard
        strategyDetailsInExpansion
        signals={signals}
        columns={commonColumns.columns}
        hidden={commonColumns.hidden}
        openId={openId}
        onToggle={(id) => setOpenId((prev) => (prev === id ? null : id))}
        renderDetail={signal => <BoardTicket signal={signal} tag="SUPER_TREND" allowTrade={!isHistoricalSim} />}
        onOpenDetail={(sig) => {
          const index = signals.findIndex(parent => parent.id === sig.id || parent.children?.some(leg => leg.id === sig.id));
          const row = rows[index];
          if (row) onSelectSignal({ token: row.token, underlying: row.underlying, timestamp_ms: row.timestamp_ms, source: row.source });
        }}
        sort={sort}
        onSortChange={setSort}
        collapsedGroups={collapsed}
        onToggleGroup={toggleGroup}
        nowMs={nowMs}
        isHistoricalSim={isHistoricalSim}
        // The three capabilities, from the operator's own Behaviour settings.
        onReorderColumn={commonColumns.onReorderColumn}
        rowScroll={commonColumns.rowScroll}
        // Date groups only: Today / Yesterday / Older. Hoisting live rows into
        // "Live now" hid those headings — a morning scan then read as one live
        // pile even when every print was from today.
        collapseOlderDays={true}
        // Trade and chart are COLUMNS now, shared with every other board, so the
        // picker can switch either off.
        renderTrade={isHistoricalSim ? undefined : rowActions.renderTrade}
        renderChart={(signal) => {
          if (!onOpenChart || !signal.instrument.quoteKey) return null;
          const index = signals.findIndex(parent => parent.id === signal.id || parent.children?.some(leg => leg.id === signal.id));
          const row = rows[index];
          const leg = row?.legs.find(item => item.option_symbol === signal.instrument.symbol);
          return <KiteActionButtons onChart={() => onOpenChart(signal.instrument.quoteKey!, 'chart', row && leg ? signalChartDataForPremiumLeg(row, leg) : undefined)} />;
        }}
        emptyLabel="No active or recent setups on the board yet."
      />
      {!hasToday && rows.length > 0 && !isHistoricalSim && (
        <div
          data-testid="supertrend-today-zero-notice"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '7px 16px',
            background: 'var(--k-surface-sunken-2, rgba(255, 255, 255, 0.02))',
            borderBottom: '1px solid var(--k-border, rgba(255, 255, 255, 0.08))',
            fontSize: 11,
            color: 'var(--k-dim, #888)',
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontWeight: 700, letterSpacing: '0.04em', color: 'var(--k-text, #eee)', textTransform: 'uppercase' }}>Today</span>
            <span style={{ padding: '1px 6px', borderRadius: 4, background: 'rgba(255, 255, 255, 0.06)', fontSize: 10, fontFamily: 'monospace' }}>0 new entries</span>
          </span>
          <span style={{ fontSize: 10.5, color: 'var(--k-dim, #888)' }}>
            No new 1H SuperTrend transitions today · Active positions from earlier sessions are tracked below
          </span>
        </div>
      )}
    </>
  );
}

export default SuperTrendSharedBoard;

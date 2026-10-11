import { useMemo } from 'react';
import { useKiteSettings } from '../../../store/useKiteSettings';
import type { ColumnId } from './SignalBoard';
import { BOARD_COL_TO_SIGNAL, SIGNAL_COL_TO_BOARD, signalColGroup, type SignalColKey } from './signalRowSpec';

/** One column order and visibility preference for SuperTrend, Snapback and Gamma Unwind. */
export function useSignalColumns() {
  const settings = useKiteSettings();
  const columns = useMemo<readonly ColumnId[]>(() => ['instrument', ...[
    ...settings.signalLeftColumnOrder, ...settings.signalRightColumnOrder,
  ].map(key => SIGNAL_COL_TO_BOARD[key as SignalColKey]).filter(Boolean) as ColumnId[]],
  [settings.signalLeftColumnOrder, settings.signalRightColumnOrder]);
  const hidden = useMemo(() => new Set<ColumnId>([...(settings.boardRowActions ? [] : ['trade', 'chart'] as ColumnId[]), ...settings.hiddenSignalCols
    .map(key => SIGNAL_COL_TO_BOARD[key as SignalColKey]).filter(Boolean) as ColumnId[]]),
  [settings.hiddenSignalCols, settings.boardRowActions]);
  const toggleColumn = (id: ColumnId) => {
    const key = BOARD_COL_TO_SIGNAL[id];
    if (key) settings.toggleSignalCol(key);
  };
  const reorder = (fromId: ColumnId, toId: ColumnId) => {
    const from = BOARD_COL_TO_SIGNAL[fromId], to = BOARD_COL_TO_SIGNAL[toId];
    if (from && to && signalColGroup(from) === signalColGroup(to)) {
      settings.reorderSignalColumn(signalColGroup(from), from, to);
    }
  };
  return { columns, hidden, toggleColumn, showAllColumns: settings.showAllSignalCols, resetColumns: settings.showAllSignalCols,
    onReorderColumn: settings.boardDragColumns ? reorder : undefined, rowScroll: settings.boardRowScroll };
}

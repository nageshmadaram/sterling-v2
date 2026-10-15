import { useMemo } from 'react';
import { useKiteSettings } from '../../../store/useKiteSettings';
import type { ColumnId } from './SignalBoard';
import { BOARD_COL_TO_SIGNAL, SIGNAL_COL_TO_BOARD, signalColGroup, type SignalColKey } from './signalRowSpec';

/** One column order and visibility preference for SuperTrend, Snapback and Gamma Unwind. */
export function useSignalColumns() {
  const settings = useKiteSettings();
  const columns = useMemo<readonly ColumnId[]>(() => ['instrument', ...[
    ...settings.signalLeftColumnOrder, ...settings.signalRightColumnOrder,
  ].map(key => SIGNAL_COL_TO_BOARD[key as SignalColKey]).filter(id => Boolean(id) && id !== 'dir') as ColumnId[]],
  [settings.signalLeftColumnOrder, settings.signalRightColumnOrder]);
  const hidden = useMemo(() => new Set<ColumnId>([...(!settings.showPriceChange ? ['chg'] as ColumnId[] : []), ...(!settings.showPriceChangePct ? ['chgPct'] as ColumnId[] : []), ...(!settings.showPriceDirection ? ['dir'] as ColumnId[] : []), ...(!settings.showExchange ? ['exchange'] as ColumnId[] : []), ...(!settings.showLeg ? ['leg'] as ColumnId[] : []), ...(settings.boardRowActions ? [] : ['trade', 'chart'] as ColumnId[]), ...settings.hiddenSignalCols
    .map(key => SIGNAL_COL_TO_BOARD[key as SignalColKey]).filter(Boolean) as ColumnId[]]),
  [settings.hiddenSignalCols, settings.boardRowActions, settings.showPriceChange, settings.showPriceChangePct, settings.showPriceDirection, settings.showExchange, settings.showLeg]);
  const toggleColumn = (id: ColumnId) => {
    const key = BOARD_COL_TO_SIGNAL[id];
    if (!key || id === 'dir') return;
    const flags = {chg:'showPriceChange',chgPct:'showPriceChangePct',exchange:'showExchange',leg:'showLeg'} as const;
    const flag = flags[id as keyof typeof flags];
    if (hidden.has(id)) {
      if (flag && !settings[flag]) settings.toggleShow(flag);
      if (settings.hiddenSignalCols.includes(key)) settings.toggleSignalCol(key);
    } else settings.toggleSignalCol(key);
  };
  const reorder = (fromId: ColumnId, toId: ColumnId) => {
    const from = BOARD_COL_TO_SIGNAL[fromId], to = BOARD_COL_TO_SIGNAL[toId];
    if (from && to && signalColGroup(from) === signalColGroup(to)) {
      settings.reorderSignalColumn(signalColGroup(from), from, to);
    }
  };
  const showAllColumns = () => {
    for (const flag of ['showPriceChange','showPriceChangePct','showExchange','showLeg'] as const) {
      if (!settings[flag]) settings.toggleShow(flag);
    }
    settings.showAllSignalCols();
  };
  return { columns, hidden, toggleColumn, showAllColumns, resetColumns: showAllColumns,
    onReorderColumn: settings.boardDragColumns ? reorder : undefined, rowScroll: settings.boardRowScroll };
}

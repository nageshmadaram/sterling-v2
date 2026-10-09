import React, { useEffect, useMemo, useState } from 'react';
import { LABELS, fmtPx, fmtQty } from '../../lib/format';
import { downloadCsv } from '../../utils/csvExport';
import { useDebounced } from '../../hooks/useDebounced';
import { fetchAllTrades, useJournalTrades } from '../../hooks/useJournal';
import {
  PAGE_SIZE, contractLabel, csvFilename, emptyTradesMessage, exitReasonHelp, exitReasonLabel, fmtHold, fmtR,
  isNarrowed, outcomeOf, sideLabel, tradeTime, tradesCsv, whenLabel, EXIT_REASON_LABELS,
  type JournalFilters, type SortKey,
} from './journalModel';
import { STRATEGY_NAMES, type JournalTrade, type Outcome } from './types';
import { TradeStory } from './TradeStory';
import { Chip, Empty, ErrorBlock, Loading, ModeBadge, PnlText, Refreshing, Section, StaleNote, controlStyle, dimText, friendlyError } from './ui';

const DASH = '—';

const SORTS: ReadonlyArray<{ value: SortKey; label: string }> = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'pnl_desc', label: 'Biggest win first' },
  { value: 'pnl_asc', label: 'Biggest loss first' },
  { value: 'hold_desc', label: 'Longest hold first' },
];

const OUTCOMES: ReadonlyArray<{ value: '' | Outcome; label: string }> = [
  { value: '', label: 'All outcomes' },
  { value: 'win', label: 'Wins' },
  { value: 'loss', label: 'Losses' },
  { value: 'open', label: 'Still open' },
];

function ExitChip({ t }: { t: JournalTrade }) {
  if (outcomeOf(t) === 'open') return <Chip tone="warn" title="The trade is still running">Open</Chip>;
  if (!t.exit_reason) return <>{DASH}</>;
  return <Chip tone="neutral" title={exitReasonHelp(t.exit_reason)}>{exitReasonLabel(t.exit_reason, t.exit_reason_label)}</Chip>;
}

function TradeRow({ t, open, onToggle, mode }: {
  t: JournalTrade; open: boolean; onToggle: () => void; mode: JournalFilters['mode'];
}) {
  const storyId = `story-${t.trade_id}`;
  const isOpen = outcomeOf(t) === 'open';
  const contract = contractLabel(t);
  const time = whenLabel(tradeTime(t));
  return (
    <>
      <tr role="row" data-testid={`trade-row-${t.trade_id}`} data-outcome={outcomeOf(t)} style={open ? { background: 'var(--k-surface)' } : undefined}>
        <td role="cell" className="jr-first" data-label="Time">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={open ? storyId : undefined}
            aria-label={`${open ? 'Hide' : 'Show'} the story of ${contract}, ${time}`}
            onClick={onToggle}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--k-ink-1)', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap' }}
          >
            <span aria-hidden style={{ display: 'inline-block', width: 10, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .12s' }}>▸</span>
            {time}
          </button>
        </td>
        <td role="cell" data-label="Strategy">{t.strategy_label || STRATEGY_NAMES[t.strategy as keyof typeof STRATEGY_NAMES] || t.strategy}</td>
        <td role="cell" data-label="View"><ModeBadge mode={t.mode} /></td>
        <td role="cell" data-label="Contract" title={t.symbol}>{contract}</td>
        <td role="cell" data-label="Side">{sideLabel(t.side)}</td>
        <td role="cell" className="jr-num" data-label={LABELS.qty}>{fmtQty(t.qty)}</td>
        <td role="cell" className="jr-num" data-label={LABELS.entry}>{fmtPx(t.entry_price)}</td>
        <td role="cell" className="jr-num" data-label={LABELS.exit}>{isOpen ? DASH : fmtPx(t.exit_price)}</td>
        <td role="cell" className="jr-num" data-label={LABELS.pnl} style={{ fontWeight: 600 }}>{isOpen ? DASH : <PnlText value={t.pnl} />}</td>
        <td role="cell" className="jr-num jr-hide-narrow" data-label="R" title="P&L as a multiple of the risk taken">{fmtR(t.r_multiple)}</td>
        <td role="cell" className="jr-num jr-hide-narrow" data-label="Hold">{fmtHold(t.hold_minutes)}</td>
        <td role="cell" data-label="Exit reason"><ExitChip t={t} /></td>
      </tr>
      {open && (
        <tr role="row" className="jr-detail-row">
          <td role="cell" className="jr-detail" colSpan={12}>
            <TradeStory trade={t} mode={mode} id={storyId} />
          </td>
        </tr>
      )}
    </>
  );
}

export function TradesTable({ trades, mode }: { trades: JournalTrade[]; mode: JournalFilters['mode'] }) {
  const [openIds, setOpenIds] = useState<ReadonlySet<string>>(new Set());
  const toggle = (id: string) => setOpenIds((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <div className="jr-scroll">
      <table className="jr-rt jr-trades" role="table" data-testid="trades-table">
        <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
          Trades. Press the time in a row to open the full story of that trade.
        </caption>
        <thead role="rowgroup">
          <tr role="row">
            <th role="columnheader" scope="col">Time</th>
            <th role="columnheader" scope="col">Strategy</th>
            <th role="columnheader" scope="col" title="LIVE is real money. SIMULATION is virtual money.">View</th>
            <th role="columnheader" scope="col">Contract</th>
            <th role="columnheader" scope="col">Side</th>
            <th role="columnheader" scope="col" className="jr-num">{LABELS.qty}</th>
            <th role="columnheader" scope="col" className="jr-num">{LABELS.entry}</th>
            <th role="columnheader" scope="col" className="jr-num">{LABELS.exit}</th>
            <th role="columnheader" scope="col" className="jr-num" title="Profit or loss after charges">{LABELS.pnl}</th>
            <th role="columnheader" scope="col" className="jr-num" title="P&L as a multiple of the risk taken">R</th>
            <th role="columnheader" scope="col" className="jr-num">Hold</th>
            <th role="columnheader" scope="col">Exit reason</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {trades.map((t) => (
            <TradeRow key={t.trade_id} t={t} open={openIds.has(t.trade_id)} onToggle={() => toggle(t.trade_id)} mode={mode} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

const field: React.CSSProperties = { ...controlStyle, flex: '1 1 130px', maxWidth: '100%' };

export function TradesSection({ filters, patch, reset, download = downloadCsv }: {
  filters: JournalFilters;
  patch: (p: Partial<JournalFilters>) => void;
  reset: () => void;
  /** Where the CSV goes; the browser download by default. */
  download?: (filename: string, csv: string) => void;
}) {
  const filterKey = JSON.stringify([filters.mode, filters.strategy, filters.range, filters.dateFrom, filters.dateTo,
    filters.outcome, filters.symbol, filters.exitReason, filters.sort]);
  const [pageState, setPageState] = useState({ key: filterKey, page: 0 });
  const page = pageState.key === filterKey ? pageState.page : 0;
  const setPage = (p: number) => setPageState({ key: filterKey, page: p });
  const q = useJournalTrades(filters, page);

  const [draft, setDraft] = useState(filters.symbol);
  const debounced = useDebounced(draft, 350);
  useEffect(() => { if (debounced !== filters.symbol) patch({ symbol: debounced }); }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (filters.symbol !== debounced) setDraft(filters.symbol); }, [filters.symbol]); // eslint-disable-line react-hooks/exhaustive-deps

  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<{ ok: boolean; text: string } | null>(null);
  const runExport = async () => {
    setExporting(true);
    setExportNote(null);
    try {
      const { trades, total, truncated } = await fetchAllTrades(filters);
      if (!trades.length) { setExportNote({ ok: false, text: 'There are no trades to export for these filters.' }); return; }
      download(csvFilename(filters.mode), tradesCsv(trades));
      setExportNote({ ok: true, text: truncated ? `Saved the first ${trades.length} of ${total} trades.` : `Saved ${trades.length} ${trades.length === 1 ? 'trade' : 'trades'}.` });
    } catch (err) {
      setExportNote({ ok: false, text: `Could not export. ${friendlyError(err)}` });
    } finally {
      setExporting(false);
    }
  };

  const data = q.data;
  const total = data?.total ?? 0;
  const from = total ? page * PAGE_SIZE + 1 : 0;
  const to = Math.min(total, page * PAGE_SIZE + (data?.items.length ?? 0));
  const empty = useMemo(() => emptyTradesMessage(filters), [filters]);

  return (
    <Section
      id="trades"
      title="Every trade"
      subtitle="Press a time to open the full story: why the app entered, what happened next and how it ended."
      actions={(
        <button type="button" onClick={runExport} disabled={exporting} style={{ ...controlStyle, cursor: exporting ? 'progress' : 'pointer', fontWeight: 600 }}>
          {exporting ? 'Preparing…' : 'Export CSV'}
        </button>
      )}
    >
      <div role="group" aria-label="Trade filters" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
        <select aria-label="Outcome" value={filters.outcome} onChange={(e) => patch({ outcome: e.target.value as '' | Outcome })} style={field}>
          {OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select aria-label="Exit reason" value={filters.exitReason} onChange={(e) => patch({ exitReason: e.target.value })} style={field}>
          <option value="">All exit reasons</option>
          {Object.entries(EXIT_REASON_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        <input
          type="search" aria-label="Search by symbol" placeholder="Search symbol, e.g. NIFTY" value={draft}
          onChange={(e) => setDraft(e.target.value)} style={{ ...field, flex: '2 1 170px' }}
        />
        <select aria-label="Sort trades" value={filters.sort} onChange={(e) => patch({ sort: e.target.value as SortKey })} style={field}>
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </div>
      <div aria-live="polite" style={{ minHeight: 18 }}>
        {exportNote && <div role={exportNote.ok ? 'status' : 'alert'} style={{ ...dimText, color: exportNote.ok ? 'var(--k-green-deep)' : 'var(--k-red-deep)', marginBottom: 6 }}>{exportNote.text}</div>}
      </div>

      {q.isLoading && <Loading what="the trades" />}
      {q.isError && !data && <ErrorBlock what="the trades" error={q.error} onRetry={() => q.refetch()} />}
      {data && data.items.length === 0 && (
        <Empty
          title={empty.title}
          action={isNarrowed(filters) ? <button type="button" onClick={reset} style={{ ...controlStyle, cursor: 'pointer' }}>Clear filters</button> : undefined}
        >{empty.body}</Empty>
      )}
      {data && data.items.length > 0 && (
        <Refreshing on={q.isFetching && q.isPlaceholderData}>
          <StaleNote show={q.isError} />
          <TradesTable trades={data.items} mode={filters.mode} />
          <nav aria-label="Trade pages" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
            <span data-testid="trades-range" style={dimText}>Showing {from}–{to} of {total}</span>
            <span style={{ display: 'inline-flex', gap: 8 }}>
              <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} style={{ ...controlStyle, cursor: page === 0 ? 'default' : 'pointer' }}>Previous</button>
              <button type="button" disabled={to >= total} onClick={() => setPage(page + 1)} style={{ ...controlStyle, cursor: to >= total ? 'default' : 'pointer' }}>Next</button>
            </span>
          </nav>
        </Refreshing>
      )}
    </Section>
  );
}

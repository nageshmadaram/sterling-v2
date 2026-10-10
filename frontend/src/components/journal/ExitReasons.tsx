import React from 'react';
import { fmtPct, fmtPnl } from '../../lib/format';
import { useJournalExitReasons } from '../../hooks/useJournal';
import { EXIT_REASON_HELP, EXIT_REASON_LABELS, exitReasonHelp, exitReasonLabel, type JournalFilters } from './journalModel';
import type { ExitReasonRow } from './types';
import { Chip, Empty, ErrorBlock, Loading, PnlText, Refreshing, Section, StaleNote, dimText } from './ui';

export function ExitReasonTable({ rows }: { rows: ExitReasonRow[] }) {
  return (
    <div className="jr-scroll">
      <table className="jr-rt" role="table" data-testid="exit-reasons-table">
        <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
          How trades ended: count, share, wins, losses and P&L for each exit reason
        </caption>
        <thead role="rowgroup">
          <tr role="row">
            <th role="columnheader" scope="col">How it ended</th>
            <th role="columnheader" scope="col" className="jr-num">Trades</th>
            <th role="columnheader" scope="col" className="jr-num">Won / lost</th>
            <th role="columnheader" scope="col" className="jr-num">Net P&amp;L</th>
            <th role="columnheader" scope="col" className="jr-num" title="Average P&L per trade that ended this way">Average</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map((r) => (
            <tr key={r.reason} role="row" data-testid={`exit-${r.reason}`}>
              <td role="cell" className="jr-first jr-wide" data-label="How it ended">
                <div style={{ fontWeight: 600, color: 'var(--k-ink-1)' }}>
                  {exitReasonLabel(r.reason, r.label)} <Chip>{r.reason}</Chip>
                </div>
                <div style={{ ...dimText, marginTop: 2, maxWidth: 560 }}>{exitReasonHelp(r.reason)}</div>
              </td>
              <td role="cell" className="jr-num" data-label="Trades">
                <div>{r.count}{r.pct_of_trades !== null && <span style={dimText}> ({fmtPct(r.pct_of_trades, 0)})</span>}</div>
                {r.pct_of_trades !== null && (
                  <div aria-hidden style={{ height: 4, borderRadius: 2, background: 'var(--k-surface-hover)', marginTop: 4, minWidth: 60 }}>
                    <div style={{ width: `${Math.min(100, r.pct_of_trades)}%`, height: '100%', borderRadius: 2, background: (r.net_pnl ?? 0) < 0 ? 'var(--k-red)' : 'var(--k-green)' }} />
                  </div>
                )}
              </td>
              <td role="cell" className="jr-num" data-label="Won / lost">{r.wins} / {r.losses}</td>
              <td role="cell" className="jr-num" data-label="Net P&L" style={{ fontWeight: 600 }}><PnlText value={r.net_pnl} /></td>
              <td role="cell" className="jr-num" data-label="Average">{r.avg_pnl === null ? '—' : fmtPnl(r.avg_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ExitReasons({ filters }: { filters: JournalFilters }) {
  const q = useJournalExitReasons(filters);
  return (
    <Section id="exit-reasons" title="How do trades end?" subtitle="Every trade ends one way: a stop, a target, the clock or a person. This shows how often each happens and what it costs or earns.">
      {q.isLoading && <Loading what="the exit reasons" />}
      {q.isError && !q.data && <ErrorBlock what="the exit reasons" error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.reasons.length === 0 && (
        <Empty title="No finished trades yet">Once trades close, each one is sorted by how it ended, for example stop-loss hit or target reached.</Empty>
      )}
      {q.data && q.data.reasons.length > 0 && (
        <Refreshing on={q.isFetching && q.isPlaceholderData}>
          <StaleNote show={q.isError} />
          <ExitReasonTable rows={q.data.reasons} />
        </Refreshing>
      )}
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--k-ink-3)' }}>What each way of ending a trade means</summary>
        <dl data-testid="exit-glossary" style={{ margin: '8px 0 0', display: 'grid', gap: 8, fontSize: 13 }}>
          {Object.keys(EXIT_REASON_HELP).map((k) => (
            <div key={k}>
              <dt style={{ fontWeight: 600, color: 'var(--k-ink-1)' }}>{EXIT_REASON_LABELS[k] ?? k}</dt>
              <dd style={{ margin: 0, ...dimText }}>{EXIT_REASON_HELP[k]}</dd>
            </div>
          ))}
        </dl>
      </details>
    </Section>
  );
}

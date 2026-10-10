import React from 'react';
import { fmtPct, fmtPnl } from '../../lib/format';
import { useJournalByStrategy, useJournalStrategyExitReasons } from '../../hooks/useJournal';
import { comparisonSentence, exitReasonLabel, leaderOf, orderedStrategies, type JournalFilters } from './journalModel';
import type { ExitReasonRow, StrategyId, StrategyRow } from './types';
import { Chip, ErrorBlock, Loading, PnlText, Refreshing, Section, StaleNote } from './ui';

const DASH = '—';

/** The way out that made this strategy the most money, or nothing when no exit has been a winner yet. */
export function bestExit(reasons: readonly ExitReasonRow[] | undefined): ExitReasonRow | null {
  if (!reasons) return null;
  const winners = reasons.filter((r) => (r.net_pnl ?? 0) > 0);
  if (!winners.length) return null;
  return winners.reduce((a, b) => ((b.net_pnl ?? 0) > (a.net_pnl ?? 0) ? b : a));
}

function profitFactor(r: StrategyRow): React.ReactNode {
  if (!r.trades) return DASH;
  if (r.profit_factor !== null) return r.profit_factor.toFixed(2);
  return r.wins > 0 && r.losses === 0 ? 'No losses' : DASH;
}

export function ComparisonTable({ rows, exits }: {
  rows: readonly StrategyRow[]; exits: Partial<Record<StrategyId, ExitReasonRow[]>>;
}) {
  const leader = leaderOf(rows);
  return (
    <div className="jr-scroll">
      <table className="jr-rt" role="table" data-testid="comparison-table">
        <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
          Strategy comparison: trades, win rate, net P&L, profit factor, average per trade and best way out
        </caption>
        <thead role="rowgroup">
          <tr role="row">
            <th role="columnheader" scope="col">Strategy</th>
            <th role="columnheader" scope="col" className="jr-num">Trades</th>
            <th role="columnheader" scope="col" className="jr-num" title="Share of finished trades that made money">Win rate</th>
            <th role="columnheader" scope="col" className="jr-num" title="Money made or lost after all costs">Net P&amp;L</th>
            <th role="columnheader" scope="col" className="jr-num" title="Rupees won for every rupee lost">Profit factor</th>
            <th role="columnheader" scope="col" className="jr-num" title="Average P&L per trade (expectancy)">Average per trade</th>
            <th role="columnheader" scope="col" title="The way of closing a trade that earned this strategy the most">Best way out</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map((r) => {
            const lead = leader === r.strategy;
            const best = bestExit(exits[r.strategy as StrategyId]);
            return (
              <tr
                key={r.strategy}
                role="row"
                data-testid={`strategy-${r.strategy}`}
                data-leader={lead ? 'true' : 'false'}
                style={lead ? { background: 'var(--k-tint-green)' } : undefined}
              >
                <td role="cell" className="jr-first" data-label="Strategy" style={{ fontWeight: 600, color: 'var(--k-ink-1)' }}>
                  {r.label} {lead && <Chip tone="good" title="Highest net P&L of the strategies that traded">Leading</Chip>}
                </td>
                <td role="cell" className="jr-num" data-label="Trades">{r.trades}</td>
                <td role="cell" className="jr-num" data-label="Win rate">{r.trades && r.win_rate_pct !== null ? fmtPct(r.win_rate_pct, 0) : DASH}</td>
                <td role="cell" className="jr-num" data-label="Net P&L" style={{ fontWeight: 600 }}>{r.trades ? <PnlText value={r.net_pnl} /> : DASH}</td>
                <td role="cell" className="jr-num" data-label="Profit factor">{profitFactor(r)}</td>
                <td role="cell" className="jr-num" data-label="Average per trade">{r.trades && r.expectancy !== null ? fmtPnl(r.expectancy) : DASH}</td>
                <td role="cell" data-label="Best way out">
                  {best ? `${exitReasonLabel(best.reason, best.label)} (${fmtPnl(best.net_pnl)})` : DASH}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function StrategyComparison({ filters }: { filters: JournalFilters }) {
  const q = useJournalByStrategy(filters);
  const exits = useJournalStrategyExitReasons(filters);
  const rows = q.data ? orderedStrategies(q.data.strategies) : [];
  return (
    <Section id="strategies" title="Which strategy is earning?" subtitle="Supertrend, Snapback and Gamma Unwind side by side for the same period.">
      {q.isLoading && <Loading what="the comparison" />}
      {q.isError && !q.data && <ErrorBlock what="the strategy comparison" error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <Refreshing on={q.isFetching && q.isPlaceholderData}>
          <StaleNote show={q.isError} />
          <p data-testid="comparison-sentence" style={{
            margin: '0 0 12px', padding: '10px 12px', borderRadius: 8, background: 'var(--k-surface)',
            border: '1px solid var(--k-border)', fontSize: 14, lineHeight: 1.5, color: 'var(--k-ink-1)',
          }}
          >{comparisonSentence(rows)}</p>
          <ComparisonTable rows={rows} exits={exits} />
        </Refreshing>
      )}
    </Section>
  );
}

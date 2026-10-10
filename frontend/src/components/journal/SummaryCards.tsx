import React from 'react';
import { fmtMoney, fmtPct, fmtPnl, pnlColor } from '../../lib/format';
import { useJournalSummary } from '../../hooks/useJournal';
import { fmtHold, type JournalFilters } from './journalModel';
import { STRATEGY_NAMES, type JournalSummary, type TradePick } from './types';
import { ErrorBlock, Loading, PnlText, Refreshing, Section, StaleNote, dimText } from './ui';

const DASH = '—';

function StatCard({ id, label, caption, children, sub }: {
  id: string; label: string; caption: string; children: React.ReactNode; sub?: React.ReactNode;
}) {
  return (
    <div role="group" aria-label={label} title={caption} data-testid={`stat-${id}`} style={{
      background: 'var(--k-surface)', border: '1px solid var(--k-border)', borderRadius: 10, padding: '12px 14px', minWidth: 0,
    }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--k-ink-4)' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, lineHeight: 1.25, margin: '4px 0 2px', color: 'var(--k-ink-1)', fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>
        {children}
      </div>
      {sub && <div style={{ fontSize: 12, color: 'var(--k-ink-4)' }}>{sub}</div>}
      <div style={{ ...dimText, marginTop: 6 }}>{caption}</div>
    </div>
  );
}

function pick(p: TradePick | null): string {
  if (!p) return '';
  return `${STRATEGY_NAMES[p.strategy as keyof typeof STRATEGY_NAMES] ?? p.strategy} · ${p.symbol}`;
}

export function SummaryGrid({ s }: { s: JournalSummary }) {
  const winsOnly = s.profit_factor === null && s.wins > 0 && s.losses === 0;
  const pnlStyle = (v: number | null | undefined) => ({ color: pnlColor(v) });
  return (
    <div className="jr-grid-cards" data-testid="summary-cards">
      <StatCard
        id="net" label="Net profit or loss"
        caption="The money made or lost after every cost. Charges and taxes are already taken off."
        sub={s.gross_pnl === null ? undefined : `Before charges ${fmtPnl(s.gross_pnl)}`}
      >
        <span style={pnlStyle(s.net_pnl)}>{fmtPnl(s.net_pnl)}</span>
      </StatCard>
      <StatCard
        id="winrate" label="Win rate"
        caption="Out of every 100 finished trades, how many ended in profit."
        sub={`${s.wins} won · ${s.losses} lost${s.breakeven ? ` · ${s.breakeven} flat` : ''}`}
      >
        {s.win_rate_pct === null ? DASH : fmtPct(s.win_rate_pct, 0)}
      </StatCard>
      <StatCard
        id="trades" label="Trades"
        caption="Finished trades in this period. A trade still running is not counted until it closes."
        sub={`${s.open + s.pending} still open`}
      >
        {s.trades}
      </StatCard>
      <StatCard
        id="pf" label="Profit factor"
        caption="Rupees won for every rupee lost. Above 1.00 means the winners outweigh the losers."
      >
        {s.profit_factor !== null ? s.profit_factor.toFixed(2) : winsOnly ? <span style={{ fontSize: 16 }}>No losses yet</span> : DASH}
      </StatCard>
      <StatCard
        id="avg" label="Average win and loss"
        caption="What a typical winning trade makes, and what a typical losing trade costs."
      >
        <div style={{ fontSize: 15, lineHeight: 1.5 }}>
          <div><span style={{ color: 'var(--k-ink-4)', fontWeight: 400 }}>Win </span><PnlText value={s.avg_win} /></div>
          <div><span style={{ color: 'var(--k-ink-4)', fontWeight: 400 }}>Loss </span><PnlText value={s.avg_loss} /></div>
        </div>
      </StatCard>
      <StatCard
        id="bestworst" label="Best and worst trade"
        caption="The single biggest win and the single biggest loss in this period."
      >
        <div style={{ fontSize: 15, lineHeight: 1.5 }}>
          <div title={pick(s.best_trade)}><span style={{ color: 'var(--k-ink-4)', fontWeight: 400 }}>Best </span><PnlText value={s.best_trade?.pnl} /></div>
          <div title={pick(s.worst_trade)}><span style={{ color: 'var(--k-ink-4)', fontWeight: 400 }}>Worst </span><PnlText value={s.worst_trade?.pnl} /></div>
        </div>
      </StatCard>
      <StatCard
        id="drawdown" label="Biggest dip"
        caption="Max drawdown: the deepest fall from the highest point the total profit reached. It shows how rough the worst patch was."
      >
        {s.trades ? fmtMoney(s.max_drawdown) : DASH}
      </StatCard>
      <StatCard
        id="hold" label="Typical time in a trade"
        caption="Average hold: how long a trade stays open before it closes."
      >
        {fmtHold(s.avg_hold_minutes)}
      </StatCard>
    </div>
  );
}

export function SummaryCards({ filters }: { filters: JournalFilters }) {
  const q = useJournalSummary(filters);
  return (
    <Section id="summary" title="How it is going" subtitle="The headline numbers for the trades that match the filters above.">
      {q.isLoading && <Loading what="the summary" />}
      {q.isError && !q.data && <ErrorBlock what="the summary" error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <StaleNote show={q.isError} />}
      {q.data && (
        <Refreshing on={q.isFetching && q.isPlaceholderData}>
          <SummaryGrid s={q.data.summary} />
        </Refreshing>
      )}
    </Section>
  );
}

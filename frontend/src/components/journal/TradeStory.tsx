import React from 'react';
import { LABELS, fmtDateTimeIST, fmtMoney, fmtPnl, fmtPx, fmtQty, fmtTimeIST, pnlColor } from '../../lib/format';
import { useJournalTrade } from '../../hooks/useJournal';
import {
  exitReasonHelp, exitReasonLabel, fmtHold, fmtR, signalFacts, timelineStep, tradePnl, whatWentWrong,
  type StepTone,
} from './journalModel';
import type { JournalMode, JournalTrade, TimelineEvent } from './types';
import { dimText, friendlyError } from './ui';

const DASH = '—';

const TONE_DOT: Record<StepTone, string> = {
  info: 'var(--k-blue)', good: 'var(--k-green)', bad: 'var(--k-red)', neutral: 'var(--k-ink-6)',
};

const h4: React.CSSProperties = { margin: '0 0 6px', fontSize: 13, fontWeight: 600, color: 'var(--k-ink-1)' };

function Facts({ rows }: { rows: ReadonlyArray<{ label: string; value: React.ReactNode }> }) {
  return (
    <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '3px 14px', fontSize: 13 }}>
      {rows.map((r) => (
        <React.Fragment key={r.label}>
          <dt style={{ color: 'var(--k-ink-4)' }}>{r.label}</dt>
          <dd style={{ margin: 0, textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--k-ink-1)' }}>{r.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/** The most the trade planned to lose: distance from entry to its first stop, times the quantity. */
export function plannedRisk(t: JournalTrade): number | null {
  if (!t.entry_price || !t.initial_sl || !t.qty) return null;
  const risk = Math.abs(t.entry_price - t.initial_sl) * t.qty;
  return risk > 0 ? risk : null;
}

export function numbersFor(t: JournalTrade): Array<{ label: string; value: React.ReactNode }> {
  const moves = (t.tsl_history || []).filter((m) => m && m.to);
  const lastMove = moves[moves.length - 1];
  const open = ['OPEN', 'PENDING'].includes(String(t.status).toUpperCase());
  const estimated = t.charges_source === 'estimate';
  const pnl = tradePnl(t);
  const rows: Array<{ label: string; value: React.ReactNode }> = [
    { label: LABELS.entry, value: fmtPx(t.entry_price) },
    { label: LABELS.qty, value: `${fmtQty(t.qty)}${t.lots ? ` (${t.lots} ${t.lots === 1 ? 'lot' : 'lots'})` : ''}` },
    { label: `${LABELS.sl} (safety limit at the start)`, value: fmtPx(t.initial_sl) },
    { label: `${LABELS.tsl} (trailing stop)`, value: moves.length ? `${fmtPx(lastMove.to)} after ${moves.length} ${moves.length === 1 ? 'move' : 'moves'}` : 'Never moved' },
    { label: LABELS.target, value: t.target ? fmtPx(t.target) : 'None set' },
    { label: LABELS.exit, value: open ? 'Still open' : fmtPx(t.exit_price) },
    { label: LABELS.risk + ' (most it planned to lose)', value: fmtMoney(plannedRisk(t)) },
    { label: LABELS.capital + ' used', value: fmtMoney(t.capital_used) },
  ];
  if (!open) {
    rows.push(
      { label: 'Gross P&L (before charges)', value: <span style={{ color: pnlColor(t.gross_pnl) }}>{fmtPnl(t.gross_pnl)}</span> },
      { label: estimated ? 'Charges and taxes (estimated)' : 'Charges and taxes', value: fmtMoney(t.charges) },
      { label: `Net ${LABELS.pnl} (what you keep)`, value: <strong style={{ color: pnlColor(pnl) }}>{fmtPnl(pnl)}</strong> },
      { label: 'R (P&L as a multiple of the risk)', value: fmtR(t.r_multiple) },
      { label: 'Time in the trade', value: fmtHold(t.hold_minutes) },
    );
    if (t.mfe !== null && t.mfe !== undefined) rows.push({ label: 'Highest it was ahead', value: fmtMoney(t.mfe) });
    if (t.mae !== null && t.mae !== undefined) rows.push({ label: 'Deepest it was behind', value: fmtMoney(t.mae) });
  }
  return rows;
}

function Timeline({ events, startDay }: { events: TimelineEvent[]; startDay: string }) {
  return (
    <ol data-testid="trade-timeline" aria-label="What happened, in order" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {events.map((e, idx) => {
        const step = timelineStep(String(e.type));
        const sameDay = e.ts_ms ? fmtDateTimeIST(e.ts_ms).slice(0, 6) === startDay : true;
        const when = e.ts_ms ? (sameDay ? fmtTimeIST(e.ts_ms) : fmtDateTimeIST(e.ts_ms)) : DASH;
        const bits: string[] = [];
        if (e.price) bits.push(`at ${fmtPx(e.price)}`);
        if (e.qty) bits.push(`qty ${fmtQty(e.qty)}`);
        return (
          <li key={`${e.type}-${e.ts_ms}-${idx}`} data-step={e.type} style={{
            display: 'grid', gridTemplateColumns: '78px 12px minmax(0, 1fr)', gap: 8, padding: '5px 0', fontSize: 13, alignItems: 'baseline',
          }}
          >
            <span style={{ color: 'var(--k-ink-4)', fontVariantNumeric: 'tabular-nums' }}>{when}</span>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: TONE_DOT[step.tone], alignSelf: 'center' }} />
            <span>
              <strong style={{ color: 'var(--k-ink-1)' }}>{step.label}</strong>
              {bits.length > 0 && <span style={{ color: 'var(--k-ink-4)' }}> · {bits.join(' · ')}</span>}
              {e.pnl !== null && e.pnl !== undefined && <span style={{ color: pnlColor(e.pnl) }}> · {fmtPnl(e.pnl)}</span>}
              {e.detail && <div style={dimText}>{e.detail}</div>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** One trade told in full: the story, why it entered, the numbers and the ordered timeline. */
export function TradeStory({ trade, mode, id }: { trade: JournalTrade; mode: JournalMode; id?: string }) {
  const detail = useJournalTrade(trade.trade_id, mode, true);
  const full: JournalTrade = detail.data?.trade ?? trade;
  const wrong = whatWentWrong(full);
  const facts = signalFacts(String(full.strategy), full.signal_context);
  const timeline = detail.data?.trade.timeline;
  const closed = String(full.status).toUpperCase() === 'CLOSED';
  const startDay = fmtDateTimeIST(full.entry_ts_ms ?? timeline?.[0]?.ts_ms).slice(0, 6);

  return (
    <div id={id} data-testid="trade-story" style={{ background: 'var(--k-surface)', border: '1px solid var(--k-border)', borderRadius: 8, padding: '12px 14px', margin: '2px 0 6px' }}>
      <p data-testid="trade-narrative" style={{ margin: '0 0 10px', fontSize: 14, lineHeight: 1.55, color: 'var(--k-ink-1)' }}>
        {full.narrative || 'No written story was recorded for this trade.'}
      </p>

      {wrong && (
        <div data-testid="what-went-wrong" role="note" style={{
          margin: '0 0 12px', padding: '10px 12px', borderRadius: 8, background: 'var(--k-tint-red)', border: '1px solid var(--k-red)',
          fontSize: 13, lineHeight: 1.5, color: 'var(--k-ink-1)',
        }}
        >
          <strong style={{ color: 'var(--k-red-deep)' }}>What went wrong. </strong>{wrong}
        </div>
      )}

      <div className="jr-two" style={{ marginBottom: 12 }}>
        <div>
          <h4 style={h4}>Why it entered</h4>
          {facts.length
            ? <Facts rows={facts.map((f) => ({ label: f.label, value: f.value }))} />
            : <div style={dimText}>No signal details were recorded for this trade.</div>}
        </div>
        <div>
          <h4 style={h4}>The numbers</h4>
          <Facts rows={numbersFor(full)} />
        </div>
      </div>

      {closed && full.exit_reason && (
        <p style={{ ...dimText, margin: '0 0 12px' }}>
          <strong style={{ color: 'var(--k-ink-3)' }}>How it ended: {exitReasonLabel(full.exit_reason, full.exit_reason_label)}.</strong>{' '}
          {exitReasonHelp(full.exit_reason)}
        </p>
      )}

      <h4 style={h4}>Step by step</h4>
      {detail.isLoading && <div role="status" style={dimText}>Loading the step-by-step timeline…</div>}
      {detail.isError && (
        <div role="alert" style={{ ...dimText, color: 'var(--k-red-deep)' }}>
          Could not load the timeline. {friendlyError(detail.error)}{' '}
          <button type="button" onClick={() => detail.refetch()} style={{ cursor: 'pointer' }}>Try again</button>
        </div>
      )}
      {timeline && timeline.length > 0 && <Timeline events={timeline} startDay={startDay} />}
      {timeline && timeline.length === 0 && <div style={dimText}>No step-by-step events were recorded for this trade.</div>}
    </div>
  );
}

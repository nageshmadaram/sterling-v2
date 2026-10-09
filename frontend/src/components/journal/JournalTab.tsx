/**
 * The Journal tab: a record of every trade, in plain English, for the live account and for the
 * Simulation (virtual money). Read-only: nothing here can place, change or cancel an order.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { fmtTimeIST } from '../../lib/format';
import {
  JOURNAL_KEY, JOURNAL_POLL_MS, useCurrentView, useJournalScope, useJournalSummary,
} from '../../hooks/useJournal';
import { defaultFilters, emptyTradesMessage, type JournalFilters } from './journalModel';
import type { JournalMode } from './types';
import { EquitySection } from './EquitySection';
import { ExitReasons } from './ExitReasons';
import { FilterBar } from './FilterBar';
import { SkippedSignals } from './SkippedSignals';
import { StrategyComparison } from './StrategyComparison';
import { SummaryCards } from './SummaryCards';
import { TradesSection } from './TradesSection';
import { Empty, JournalStyles, Section, controlStyle, dimText } from './ui';

const ALL_MODES: readonly JournalMode[] = ['live', 'simulation', 'all'];

/** Shown instead of the numbers when nothing has traded yet: it teaches what each part will show. */
function NothingYet({ filters }: { filters: JournalFilters }) {
  const msg = emptyTradesMessage(filters);
  return (
    <Section id="nothing-yet" title="Nothing to show yet">
      <Empty title={msg.title}>{msg.body}</Empty>
      <div style={{ ...dimText, marginTop: 12, fontSize: 13 }}>
        <div style={{ fontWeight: 600, color: 'var(--k-ink-1)', marginBottom: 4 }}>What this page will show once trades happen</div>
        <ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 3 }}>
          <li>How it is going: profit or loss, how often trades win, and the biggest win and loss.</li>
          <li>Which strategy is earning: Supertrend, Snapback and Gamma Unwind side by side.</li>
          <li>Profit over time, and how each day ended.</li>
          <li>Every trade, with the full story of why it entered and how it ended.</li>
          <li>Signals the app skipped, with the reason (listed below when there are any).</li>
        </ul>
      </div>
    </Section>
  );
}

export function JournalTab() {
  const view = useCurrentView();
  const scope = useJournalScope();
  const qc = useQueryClient();
  const [filters, setFilters] = useState<JournalFilters>(() => defaultFilters(view));

  // The header's Live / Simulation switch decides what the Journal opens on, and moves it when flipped.
  const lastView = useRef(view);
  useEffect(() => {
    if (lastView.current === view) return;
    lastView.current = view;
    setFilters((f) => ({ ...f, mode: view }));
  }, [view]);

  const modes = scope.data?.modes?.length ? scope.data.modes : ALL_MODES;
  // A person who may not read a view is never left sitting on it.
  useEffect(() => {
    if (!modes.includes(filters.mode)) setFilters((f) => ({ ...f, mode: modes[0] }));
  }, [modes, filters.mode]);

  const patch = useCallback((p: Partial<JournalFilters>) => setFilters((f) => ({ ...f, ...p })), []);
  const reset = useCallback(() => setFilters((f) => ({
    ...defaultFilters(f.mode), range: f.range, dateFrom: f.dateFrom, dateTo: f.dateTo,
  })), []);

  const summary = useJournalSummary(filters);
  const s = summary.data?.summary;
  const nothing = !!s && s.trades === 0 && s.open === 0 && s.pending === 0;

  return (
    <div className="jr-root" data-testid="journal-tab" style={{ height: '100%', overflowY: 'auto', padding: '14px 16px 48px', background: 'var(--k-bg)', color: 'var(--k-text)' }}>
      <JournalStyles />
      <div style={{ maxWidth: 1280, margin: '0 auto' }}>
        <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600, color: 'var(--k-ink-1)' }}>Trade Journal</h1>
            <div style={{ ...dimText, fontSize: 13, marginTop: 2 }}>
              A record of every trade the app made: what it did, why, and how it turned out.
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span data-testid="journal-updated" style={dimText}>
              {summary.dataUpdatedAt ? `Updated ${fmtTimeIST(summary.dataUpdatedAt)} IST` : ''}
              {` · refreshes every ${JOURNAL_POLL_MS / 1000} s while the market is open`}
            </span>
            <button type="button" onClick={() => qc.invalidateQueries({ queryKey: JOURNAL_KEY })} style={{ ...controlStyle, cursor: 'pointer' }}>Refresh</button>
          </div>
        </header>

        <FilterBar filters={filters} patch={patch} modes={modes} />

        {nothing ? <NothingYet filters={filters} /> : (
          <>
            <SummaryCards filters={filters} />
            <StrategyComparison filters={filters} />
            <EquitySection filters={filters} />
            <TradesSection filters={filters} patch={patch} reset={reset} />
            <ExitReasons filters={filters} />
          </>
        )}
        <SkippedSignals filters={filters} />
      </div>
    </div>
  );
}

export default JournalTab;

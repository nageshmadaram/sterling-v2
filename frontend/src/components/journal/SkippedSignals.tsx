import React, { useState } from 'react';
import { fmtDateTimeIST } from '../../lib/format';
import { useJournalSkips } from '../../hooks/useJournal';
import { SKIPS_PAGE_SIZE, type JournalFilters } from './journalModel';
import type { SkipItem } from './types';
import { Chip, Empty, ErrorBlock, Loading, Refreshing, Section, StaleNote, controlStyle, dimText } from './ui';

function SkipRow({ s }: { s: SkipItem }) {
  return (
    <li data-testid={`skip-${s.id}`} style={{ padding: '10px 0', borderBottom: '1px solid var(--k-surface-hover)' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', fontSize: 13 }}>
        <strong style={{ color: 'var(--k-ink-1)' }}>{s.strategy_label || s.strategy}</strong>
        {(s.symbol || s.underlying) && <span style={{ color: 'var(--k-ink-3)' }}>{s.symbol || s.underlying}</span>}
        {s.count > 1 && <Chip title="How many times this same signal was declined">seen {s.count} times</Chip>}
        <span style={{ ...dimText, marginLeft: 'auto' }}>{fmtDateTimeIST(s.last_ms, false)}</span>
      </div>
      <div style={{ fontSize: 13, color: 'var(--k-ink-1)', marginTop: 2 }}>{s.reason || 'No reason was recorded.'}</div>
    </li>
  );
}

export function SkippedSignals({ filters }: { filters: JournalFilters }) {
  const key = JSON.stringify([filters.mode, filters.strategy, filters.range, filters.dateFrom, filters.dateTo]);
  const [state, setState] = useState({ key, page: 0 });
  const page = state.key === key ? state.page : 0;
  const q = useJournalSkips(filters, page);
  const data = q.data;
  const total = data?.total ?? 0;
  const from = total ? page * SKIPS_PAGE_SIZE + 1 : 0;
  const to = Math.min(total, page * SKIPS_PAGE_SIZE + (data?.items.length ?? 0));

  return (
    <Section
      id="skips"
      title="Signals the app skipped"
      subtitle="Sometimes a strategy sees a signal but does not trade it, because a safety check or a filter says no. Each one is listed here with the reason."
    >
      {q.isLoading && <Loading what="the skipped signals" />}
      {q.isError && !data && <ErrorBlock what="the skipped signals" error={q.error} onRetry={() => q.refetch()} />}
      {data && data.items.length === 0 && (
        <Empty title="No skipped signals in this period">
          When a strategy sees a signal but a safety check or filter stops it, the signal and the plain-English reason appear here. Nothing is hidden.
        </Empty>
      )}
      {data && data.items.length > 0 && (
        <Refreshing on={q.isFetching && q.isPlaceholderData}>
          <StaleNote show={q.isError} />
          {data.by_code.length > 0 && (
            <div data-testid="skip-reasons" style={{ marginBottom: 8 }}>
              <div style={{ ...dimText, marginBottom: 4 }}>Most common reasons</div>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 2, fontSize: 13 }}>
                {data.by_code.slice(0, 5).map((c) => (
                  <li key={c.code}><strong>{c.signals}</strong> {c.signals === 1 ? 'signal' : 'signals'}: {c.reason}</li>
                ))}
              </ul>
            </div>
          )}
          <ul data-testid="skip-list" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.items.map((s) => <SkipRow key={s.id} s={s} />)}
          </ul>
          <nav aria-label="Skipped signal pages" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
            <span style={dimText}>Showing {from}–{to} of {total}</span>
            <span style={{ display: 'inline-flex', gap: 8 }}>
              <button type="button" disabled={page === 0} onClick={() => setState({ key, page: page - 1 })} style={{ ...controlStyle, cursor: page === 0 ? 'default' : 'pointer' }}>Previous</button>
              <button type="button" disabled={to >= total} onClick={() => setState({ key, page: page + 1 })} style={{ ...controlStyle, cursor: to >= total ? 'default' : 'pointer' }}>Next</button>
            </span>
          </nav>
        </Refreshing>
      )}
    </Section>
  );
}

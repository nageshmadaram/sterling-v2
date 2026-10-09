import React, { useId, useRef, useState } from 'react';
import { fmtPnl, pnlColor } from '../../lib/format';
import { useJournalCalendar, useJournalEquity } from '../../hooks/useJournal';
import type { JournalFilters } from './journalModel';
import {
  buildEquitySeries, calendarSentence, curveSummary, dayIntensity, fmtDayLabel, indexAt, toPath, xAt, yAt, yDomain,
  type Series,
} from './chartModel';
import type { CalendarDay, EquityResponse } from './types';
import { Empty, ErrorBlock, Loading, Refreshing, Section, StaleNote, dimText } from './ui';

const PLOT_HEIGHT = 200;

/**
 * Total profit over time as a small accessible SVG: no chart library, no new dependency. The line is
 * drawn in a 0..100 box that stretches to fit; the labels and markers are plain HTML so they stay
 * sharp and readable at any width. Arrow keys (or a finger or mouse) read each point aloud.
 */
export function EquityChart({ series }: { series: Series[] }) {
  const readoutId = useId();
  const plotRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const n = series[0]?.points.length ?? 0;
  const dom = yDomain(series);
  const i = active === null ? n - 1 : Math.min(active, n - 1);
  const zero = yAt(0, dom);

  const move = (clientX: number) => {
    const rect = plotRef.current?.getBoundingClientRect();
    if (!rect || !rect.width) return;
    setActive(indexAt((clientX - rect.left) / rect.width, n));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); setActive(Math.max(0, i - 1)); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); setActive(Math.min(n - 1, i + 1)); }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
    else if (e.key === 'End' || e.key === 'Escape') { e.preventDefault(); setActive(null); }
  };

  return (
    <div>
      <div id={readoutId} aria-live="polite" data-testid="equity-readout" style={{ fontSize: 13, minHeight: 36, marginBottom: 6, color: 'var(--k-ink-1)' }}>
        {series.map((s) => {
          const p = s.points[Math.min(i, s.points.length - 1)];
          return (
            <div key={s.key}>
              <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: s.color, marginRight: 6 }} aria-hidden />
              <strong>{s.label}</strong> · {p.label}: <span style={{ color: pnlColor(p.cum), fontWeight: 600 }}>{fmtPnl(p.cum)}</span>
              {p.pnl !== null && <span style={dimText}> (this step {fmtPnl(p.pnl)})</span>}
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <div aria-hidden style={{ position: 'relative', width: 84, height: PLOT_HEIGHT, flex: '0 0 auto', fontSize: 11, color: 'var(--k-dim)', textAlign: 'right' }}>
          <span style={{ position: 'absolute', right: 0, top: `${yAt(dom.max, dom)}%` }}>{fmtPnl(dom.max, 0)}</span>
          <span style={{ position: 'absolute', right: 0, top: `${zero}%`, transform: 'translateY(-50%)', color: 'var(--k-ink-4)' }}>₹0</span>
          <span style={{ position: 'absolute', right: 0, bottom: 0 }}>{fmtPnl(dom.min, 0)}</span>
        </div>
        <div
          ref={plotRef}
          tabIndex={0}
          role="group"
          aria-label="Profit curve. Use the left and right arrow keys to read each point."
          aria-describedby={readoutId}
          data-testid="equity-plot"
          onKeyDown={onKey}
          onPointerMove={(e) => move(e.clientX)}
          onPointerDown={(e) => move(e.clientX)}
          onPointerLeave={() => setActive(null)}
          onBlur={() => setActive(null)}
          style={{ position: 'relative', flex: 1, minWidth: 0, height: PLOT_HEIGHT, touchAction: 'pan-y', cursor: 'crosshair', background: 'var(--k-surface)', borderRadius: 6 }}
        >
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height="100%" role="img" aria-label={curveSummary(series)} style={{ display: 'block' }}>
            <line x1="0" x2="100" y1={zero} y2={zero} stroke="var(--k-border-strong-3)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
            {series.map((s) => (
              <path key={s.key} d={toPath(s.points, dom)} fill="none" stroke={s.color} strokeWidth="2.2"
                strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" data-testid={`equity-line-${s.key}`} />
            ))}
          </svg>
          <div aria-hidden style={{ position: 'absolute', top: 0, bottom: 0, left: `${xAt(i, n)}%`, width: 1, background: 'var(--k-ink-6)', opacity: 0.5 }} />
          {series.map((s) => {
            const p = s.points[Math.min(i, s.points.length - 1)];
            return (
              <span key={s.key} aria-hidden style={{
                position: 'absolute', left: `${xAt(i, n)}%`, top: `${yAt(p.cum, dom)}%`, width: 10, height: 10, marginLeft: -5, marginTop: -5,
                borderRadius: '50%', background: s.color, border: '2px solid var(--k-bg)',
              }}
              />
            );
          })}
        </div>
      </div>
      <div aria-hidden style={{ display: 'flex', justifyContent: 'space-between', marginLeft: 92, ...dimText, fontSize: 11, marginTop: 4 }}>
        <span>{series[0]?.points[1]?.label ?? ''}</span>
        <span>{series[0]?.points[n - 1]?.label ?? ''}</span>
      </div>
    </div>
  );
}

/** The same curve as a table, for anyone who would rather read exact figures (and for touch screens). */
export function EquityTable({ eq }: { eq: EquityResponse }) {
  const modes = Object.entries(eq.by_mode ?? {}).filter(([, c]) => c.by_day.length);
  const multi = modes.length >= 2;
  const days = multi ? [...new Set(modes.flatMap(([, c]) => c.by_day.map((d) => d.day)))].sort() : eq.by_day.map((d) => d.day);
  return (
    <details style={{ marginTop: 10 }}>
      <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--k-ink-3)' }}>See the numbers day by day</summary>
      <div className="jr-scroll" style={{ marginTop: 6 }}>
        <table className="jr-rt" role="table" data-testid="equity-table">
          <thead role="rowgroup">
            <tr role="row">
              <th role="columnheader" scope="col">Day</th>
              {multi
                ? modes.map(([m]) => <th key={m} role="columnheader" scope="col" className="jr-num">{m === 'LIVE' ? 'Live' : 'Simulation'} total</th>)
                : (<><th role="columnheader" scope="col" className="jr-num">That day</th><th role="columnheader" scope="col" className="jr-num">Total so far</th></>)}
            </tr>
          </thead>
          <tbody role="rowgroup">
            {days.map((day) => (
              <tr key={day} role="row">
                <td role="cell" className="jr-first" data-label="Day">{fmtDayLabel(day)}</td>
                {multi
                  ? modes.map(([m, c]) => {
                    const upTo = c.by_day.filter((d) => d.day <= day).pop();
                    return <td key={m} role="cell" className="jr-num" data-label={`${m === 'LIVE' ? 'Live' : 'Simulation'} total`} style={{ color: pnlColor(upTo?.cum_pnl) }}>{fmtPnl(upTo?.cum_pnl ?? 0)}</td>;
                  })
                  : (() => {
                    const d = eq.by_day.find((x) => x.day === day)!;
                    return (<>
                      <td role="cell" className="jr-num" data-label="That day" style={{ color: pnlColor(d.pnl) }}>{fmtPnl(d.pnl)}</td>
                      <td role="cell" className="jr-num" data-label="Total so far" style={{ color: pnlColor(d.cum_pnl) }}>{fmtPnl(d.cum_pnl)}</td>
                    </>);
                  })()}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function CalendarStrip({ days }: { days: CalendarDay[] }) {
  const [active, setActive] = useState<string | null>(null);
  const maxAbs = Math.max(0, ...days.map((d) => Math.abs(d.net_pnl ?? 0)));
  const shown = days.find((d) => d.day === active) ?? days[days.length - 1];
  let lastMonth = '';
  return (
    <div>
      <ul data-testid="calendar-strip" aria-label="Profit or loss for each day that had trades" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {days.map((d) => {
          const net = d.net_pnl ?? 0;
          const tone = net > 0 ? 'var(--k-green)' : net < 0 ? 'var(--k-red)' : 'var(--k-border-strong)';
          const month = d.day.slice(0, 7);
          const showMonth = month !== lastMonth;
          lastMonth = month;
          return (
            <li key={d.day} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
              <span aria-hidden style={{ fontSize: 10, color: 'var(--k-dim)', height: 12 }}>{showMonth ? fmtDayLabel(d.day).split(' ')[2] : ''}</span>
              <button
                type="button"
                aria-label={calendarSentence(d)}
                title={calendarSentence(d)}
                data-testid={`day-${d.day}`}
                onMouseEnter={() => setActive(d.day)}
                onFocus={() => setActive(d.day)}
                onClick={() => setActive(d.day)}
                style={{
                  width: 34, height: 34, borderRadius: 6, cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--k-ink-1)',
                  border: `1px solid ${d.day === shown?.day ? 'var(--k-ink-1)' : 'var(--k-border)'}`,
                  background: `color-mix(in srgb, ${tone} ${dayIntensity(d.net_pnl, maxAbs)}%, var(--k-bg))`,
                }}
              >{d.day.slice(8, 10)}</button>
            </li>
          );
        })}
      </ul>
      <div aria-live="polite" data-testid="calendar-readout" style={{ marginTop: 8, fontSize: 13, color: 'var(--k-ink-1)' }}>
        {shown ? calendarSentence(shown) : ''}
      </div>
      <div style={{ ...dimText, marginTop: 2 }}>Green days made money, red days lost money. The deeper the colour, the bigger the day.</div>
    </div>
  );
}

export function EquitySection({ filters }: { filters: JournalFilters }) {
  const eq = useJournalEquity(filters);
  const cal = useJournalCalendar(filters);
  const series = buildEquitySeries(eq.data);
  return (
    <Section id="equity" title="Profit over time" subtitle="Total profit or loss as each trade closes, and how each day ended.">
      {eq.isLoading && <Loading what="the profit curve" />}
      {eq.isError && !eq.data && <ErrorBlock what="the profit curve" error={eq.error} onRetry={() => eq.refetch()} />}
      {eq.data && !series.length && (
        <Empty title="No closed trades to draw yet">The line appears once a trade closes. It starts at ₹0 and moves up or down with every finished trade.</Empty>
      )}
      {eq.data && series.length > 0 && (
        <Refreshing on={eq.isFetching && eq.isPlaceholderData}>
          <StaleNote show={eq.isError} />
          <EquityChart series={series} />
          <EquityTable eq={eq.data} />
        </Refreshing>
      )}

      <h3 style={{ margin: '16px 0 8px', fontSize: 14, fontWeight: 600, color: 'var(--k-ink-1)' }}>Day by day</h3>
      {cal.isLoading && <Loading what="the daily results" />}
      {cal.isError && !cal.data && <ErrorBlock what="the daily results" error={cal.error} onRetry={() => cal.refetch()} />}
      {cal.data && !cal.data.days.length && <div style={dimText}>No day has a closed trade yet.</div>}
      {cal.data && cal.data.days.length > 0 && (
        <Refreshing on={cal.isFetching && cal.isPlaceholderData}>
          <CalendarStrip days={cal.data.days} />
        </Refreshing>
      )}
    </Section>
  );
}

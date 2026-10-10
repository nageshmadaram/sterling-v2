/**
 * Geometry and labels for the equity curve and the daily strip. Pure, so the shapes the SVG draws and
 * the sentences read out are checked in the tests rather than by eye.
 */
import { fmtDateTimeIST, fmtPnl } from '../../lib/format';
import type { CalendarDay, EquityResponse } from './types';

export interface CurvePoint { label: string; pnl: number | null; cum: number }
export interface Series { key: string; label: string; color: string; points: CurvePoint[] }

const MODE_STYLE: Record<string, { label: string; color: string }> = {
  LIVE: { label: 'Live', color: 'var(--k-brand)' },
  SIMULATION: { label: 'Simulation', color: 'var(--k-violet)' },
};

const START: CurvePoint = { label: 'Start', pnl: null, cum: 0 };

const DAY_FMT = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC', weekday: 'short', day: '2-digit', month: 'short',
});

/** "Mon 06 Oct" for a YYYY-MM-DD day (read as a calendar date, so no time zone can shift it). */
export function fmtDayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  if (!y || !m || !d) return day;
  return DAY_FMT.format(new Date(Date.UTC(y, m - 1, d, 12))).replace(',', '');
}

/**
 * The lines to draw. One view -> one line, a point per closed trade (starting at zero).
 * Live and Simulation together -> one line each, a point per day on a shared day axis.
 */
export function buildEquitySeries(eq: EquityResponse | undefined): Series[] {
  if (!eq) return [];
  const modes = Object.entries(eq.by_mode ?? {}).filter(([, c]) => c.by_day.length > 0);
  if (modes.length >= 2) {
    const days = [...new Set(modes.flatMap(([, c]) => c.by_day.map((d) => d.day)))].sort();
    return modes.map(([mode, curve]) => {
      const byDay = new Map(curve.by_day.map((d) => [d.day, d]));
      let cum = 0;
      const points: CurvePoint[] = [START];
      for (const day of days) {
        const hit = byDay.get(day);
        if (hit) cum = hit.cum_pnl ?? cum;
        points.push({ label: fmtDayLabel(day), pnl: hit?.pnl ?? null, cum });
      }
      const style = MODE_STYLE[mode.toUpperCase()] ?? { label: mode, color: 'var(--k-blue)' };
      return { key: mode, ...style, points };
    });
  }
  if (!eq.by_trade.length) return [];
  const mode = String(eq.mode || '').toUpperCase();
  const style = MODE_STYLE[mode];
  return [{
    key: mode || 'ALL',
    label: style ? `${style.label} total` : 'Total profit so far',
    color: 'var(--k-blue)',
    points: [START, ...eq.by_trade.map((t) => ({
      label: t.ts_ms ? fmtDateTimeIST(t.ts_ms, false) : fmtDayLabel(t.day),
      pnl: t.pnl,
      cum: t.cum_pnl ?? 0,
    }))],
  }];
}

export interface Domain { min: number; max: number }

/** The value range to plot: always includes zero, with a little air so the line never touches the edge. */
export function yDomain(series: readonly Series[]): Domain {
  const values = series.flatMap((s) => s.points.map((p) => p.cum));
  let min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (max === min) { max += 1; min -= 1; }
  const pad = (max - min) * 0.08;
  return { min: min - pad, max: max + pad };
}

/** 0..100 across the plot (a single point sits in the middle). */
export function xAt(i: number, n: number): number {
  return n <= 1 ? 50 : (i / (n - 1)) * 100;
}

/** 0 at the top, 100 at the bottom. */
export function yAt(v: number, d: Domain): number {
  return ((d.max - v) / (d.max - d.min)) * 100;
}

export function toPath(points: readonly CurvePoint[], d: Domain): string {
  return points
    .map((p, i) => `${i ? 'L' : 'M'}${xAt(i, points.length).toFixed(2)} ${yAt(p.cum, d).toFixed(2)}`)
    .join(' ');
}

/** The index under a pointer at ``fraction`` (0..1) across the plot. */
export function indexAt(fraction: number, n: number): number {
  if (n <= 1) return 0;
  return Math.max(0, Math.min(n - 1, Math.round(fraction * (n - 1))));
}

/** One sentence for a screen reader: where the total started, where it is, and how many points. */
export function curveSummary(series: readonly Series[]): string {
  if (!series.length) return 'No profit curve yet.';
  return series.map((s) => {
    const last = s.points[s.points.length - 1];
    const steps = s.points.length - 1;
    return `${s.label}: ${fmtPnl(0)} at the start, ${fmtPnl(last.cum)} now, over ${steps} ${steps === 1 ? 'point' : 'points'}.`;
  }).join(' ');
}

/** Opacity (as a percent of the colour) for a day cell: bigger days are darker, never invisible. */
export function dayIntensity(net: number | null, maxAbs: number): number {
  if (net === null || !maxAbs) return 18;
  return Math.round(18 + Math.min(1, Math.abs(net) / maxAbs) * 62);
}

export function calendarSentence(day: CalendarDay): string {
  const pnl = fmtPnl(day.net_pnl);
  const parts = [`${fmtDayLabel(day.day)}: ${pnl}`, `${day.trades} ${day.trades === 1 ? 'trade' : 'trades'}`];
  if (day.wins || day.losses) parts.push(`${day.wins} won, ${day.losses} lost`);
  return parts.join(' · ');
}

import React from 'react';
import { CandlestickSeries, ColorType, createChart, LineSeries, LineStyle, type Time } from 'lightweight-charts';
import { useEmaTrendChart } from '../../hooks/useEmaTrend';
import { useSimNowMs } from '../../hooks/useReplayStore';
import { EMA_TREND_MODE_LABEL, type EmaTrendFeature, type EmaTrendMode } from '../../types/emaTrend';
import { fmtPx } from '../../lib/format';

/** Project the exact server evidence; never recompute EMA/ST or use a forming HTF bar here. */
export function emaTrendChartSeries(features: readonly EmaTrendFeature[]) {
  const ordered = [...features].sort((a, b) => a.bar.close_ms - b.bar.close_ms);
  const line = (key: 'ema10' | 'ema21' | 'dema50' | 'st_line' | 'context_st_line') => ordered.flatMap(f => {
    const value = f[key];
    return value != null && Number.isFinite(value) ? [{ time: Math.floor(f.bar.close_ms / 1000) as Time, value }] : [];
  });
  return { candles: ordered.map(f => ({ time: Math.floor(f.bar.close_ms / 1000) as Time,
    open: f.bar.open, high: f.bar.high, low: f.bar.low, close: f.bar.close })),
    ema10: line('ema10'), ema21: line('ema21'), dema50: line('dema50'), st: line('st_line'), context: line('context_st_line') };
}
export function EmaTrendChart({ symbol, mode, signalId, configRevision }: { symbol: string; mode: EmaTrendMode; signalId?: string; configRevision?: number }) {
  const query = useEmaTrendChart(symbol, mode, signalId, configRevision);
  const replay = useSimNowMs() !== null;
  const ref = React.useRef<HTMLDivElement>(null);
  const data = React.useMemo(() => emaTrendChartSeries(query.data?.features || []), [query.data]);
  const latest = query.data?.features?.at(-1);
  React.useEffect(() => {
    const host = ref.current;
    if (!host || !data.candles.length || replay) return;
    const styles = getComputedStyle(host);
    const chart = createChart(host, { autoSize: true, height: 420,
      layout: { background: { type: ColorType.Solid, color: styles.getPropertyValue('--k-bg').trim() || '#10151c' }, textColor: styles.getPropertyValue('--k-text').trim() || '#bfc8d5' },
      timeScale: { timeVisible: mode !== 'swing', secondsVisible: false } });
    chart.addSeries(CandlestickSeries, { upColor: '#278a57', downColor: '#c34e4e', borderVisible: false, wickUpColor: '#278a57', wickDownColor: '#c34e4e' }).setData(data.candles);
    for (const [title, color, values] of [
      ['EMA10', '#f39424', data.ema10], ['EMA21', '#27a65c', data.ema21],
      ['DEMA50', '#338cde', data.dema50], ['ST (10,3)', '#a66ce6', data.st],
    ] as const) chart.addSeries(LineSeries, { title, color, lineWidth: 2, priceLineVisible: false }).setData([...values]);
    if (data.context.length) chart.addSeries(LineSeries, { title: 'Completed HTF ST', color: '#d3a1ff', lineStyle: LineStyle.Dashed, lineWidth: 2, priceLineVisible: false }).setData(data.context);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, replay, mode]);
  return <section aria-label="EMA Trend evidence chart" style={{ padding: 12, height: '100%', overflow: 'auto' }}>
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 12 }}>
      <strong>{symbol} · {EMA_TREND_MODE_LABEL[mode]} · PAPER</strong>
      <span style={{ color: '#f39424' }}>EMA10</span><span style={{ color: '#27a65c' }}>EMA21</span>
      <span style={{ color: '#338cde' }}>DEMA50</span><span style={{ color: '#a66ce6' }}>Supertrend (10,3)</span>
    </div>
    <p style={{ fontSize: 11 }}>Ordinary completed candles and server-computed strategy values. {signalId ? 'Frozen to the selected signal close.' : 'Recorded campaign inspection.'} This chart does not place orders or change global chart preferences.</p>
    {replay ? <p role="status">Historical replay is read-only; live chart data is not loaded.</p>
      : query.isError ? <p role="alert">Chart unavailable: {(query.error as Error).message}</p>
      : query.isLoading ? <p role="status">Loading completed-bar evidence…</p>
      : !data.candles.length ? <p role="status">No recorded feature series. Scan this instrument and profile first.</p> : null}
    <div ref={ref} style={{ minHeight: data.candles.length ? 420 : 0 }} />
    {latest && <div style={{ fontSize: 12 }}>
      <p>Signal ST: {latest.st_direction} at {fmtPx(latest.st_line)} · completed context ST: {latest.context_st}{latest.context_st_line != null ? ` at ${fmtPx(latest.context_st_line)}` : ''}.</p>
      <p>Context close: {latest.context_close_ms ? new Date(latest.context_close_ms).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : 'Unavailable'} · {latest.context_count} completed context bars · {latest.ready ? 'feature ready' : 'warming up'}.</p>
    </div>}
    {(query.data?.blockers || []).map(reason => <p key={reason} style={{ fontSize: 11 }}>{reason.replace(/_/g, ' ')}</p>)}
  </section>;
}

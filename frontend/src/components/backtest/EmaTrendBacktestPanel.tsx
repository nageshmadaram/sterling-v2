import React from 'react';
import { useRunUnifiedBacktest } from '../../hooks/useUnifiedBacktest';
import { type EmaTrendMode } from '../../types/emaTrend';
import { EmaTrendReport } from './EmaTrendReport';

const TF: Record<EmaTrendMode, string> = { swing: 'day', intraday: '15m', scalping: '3m', ultra_scalping: '1m' };
export function EmaTrendBacktestPanel({ onBack }: { onBack: () => void }) {
  const run = useRunUnifiedBacktest();
  const [symbol, setSymbol] = React.useState('NSE:RELIANCE');
  const [mode, setMode] = React.useState<EmaTrendMode>('swing');
  const [capital, setCapital] = React.useState(100000);
  const [lookback, setLookback] = React.useState(365);
  const valid = /^(NSE|BSE|NFO|BFO):[A-Z0-9&_.-]+$/.test(symbol) && capital > 0 && lookback >= 1;
  return <section aria-label="EMA Trend backtest" style={{ padding: 16, overflow: 'auto', height: '100%', fontSize: 12 }}>
    <button type="button" className="sb-tool" onClick={onBack}>Back to strategy selection</button>
    <h2>10/21 EMA + Supertrend · selected-contract replay</h2>
    <p>Native campaign simulation with partial exits, add-backs and estimated fees. Historical fills are modelled; economic evidence remains inconclusive.</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (!valid) return;
      run.mutate({ strategy: 'ema_trend', symbol, instrument_scope: 'single', contract_type: symbol.startsWith('NFO:') || symbol.startsWith('BFO:') ? 'auto' : 'spot',
        data_source: 'kite', dynamic_mode: true, timeframe: TF[mode], lookback_days: lookback,
        starting_capital: capital, lot_size: 1, num_lots: 1, slippage_points: 0, brokerage_per_order: 0,
        stt_pct: 0, session_cutoff_hour: 15, session_cutoff_min: 15, strategy_params: { mode } });
    }} style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
      <label>Cash equity<input aria-label="Backtest exact instrument" value={symbol} onChange={event => setSymbol(event.target.value.toUpperCase())} required /></label>
      <label>Profile<select aria-label="Backtest EMA Trend profile" value={mode} onChange={event => setMode(event.target.value as EmaTrendMode)}>{Object.entries({ swing: 'Original · Daily / Weekly' }).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>Paper capital (₹)<input type="number" aria-label="Backtest paper capital" min={1} max={100000000} value={capital} onChange={event => setCapital(Number(event.target.value))} /></label>
      <label>History days<input type="number" aria-label="Backtest history days" min={1} max={3650} value={lookback} onChange={event => setLookback(Number(event.target.value))} /></label>
      <button type="submit" className="sb-tool" disabled={!valid || run.isPending}>{run.isPending ? 'Replaying…' : 'Run EMA Trend backtest'}</button>
    </form>
    <p>Signal timeframe: {TF[mode]}. Strategy-owned costs and sizing apply; derivative lots and legacy stop/target settings do not.</p>
    {run.error && <p role="alert">Backtest unavailable: {run.error.message}</p>}
    {run.data && ('format' in run.data && run.data.format === 'EMA_TREND_LIFECYCLE_V1'
      ? <EmaTrendReport report={run.data.report} /> : <p role="alert">The server returned an incompatible report. No legacy result was substituted.</p>)}
  </section>;
}

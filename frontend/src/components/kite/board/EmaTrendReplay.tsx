import React from 'react';
import { useEmaTrendConfig, useEmaTrendReplay, useEmaTrendRun, useEmaTrendRuns } from '../../../hooks/useEmaTrend';
import { EMA_TREND_MODE_LABEL, type EmaTrendMode } from '../../../types/emaTrend';
import { EmaTrendReport } from '../../backtest/EmaTrendReport';

export function EmaTrendReplay() {
  const config = useEmaTrendConfig();
  const replay = useEmaTrendReplay();
  const runs = useEmaTrendRuns();
  const [symbol, setSymbol] = React.useState('');
  const [mode, setMode] = React.useState<EmaTrendMode>('swing');
  const [runId, setRunId] = React.useState('');
  const run = useEmaTrendRun(runId || replay.data?.id);
  const selectedSymbol = symbol || config.data?.config.symbols[0] || '';
  const result = run.data || replay.data;
  return <section aria-label="EMA Trend recorded replay" style={{ padding: 12 }}>
    <p>Replay recorded completed bars through the same campaign rules. The result remains read-only and cannot arm orders.</p>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <select aria-label="Replay equity" value={selectedSymbol} onChange={event => setSymbol(event.target.value)}>{(config.data?.config.symbols || []).map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label="Replay profile" value={mode} onChange={event => setMode(event.target.value as EmaTrendMode)}>{Object.entries(EMA_TREND_MODE_LABEL).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
      <button type="button" className="sb-tool" disabled={!selectedSymbol || !config.data || replay.isPending}
        onClick={() => { if (config.data) { setRunId(''); replay.mutate({ symbol: selectedSymbol, mode, expected_config_revision: config.data.revision }); } }}>Replay recorded bars</button>
      <select aria-label="Saved replay run" value={runId} onChange={event => setRunId(event.target.value)}><option value="">Latest requested run</option>{(runs.data?.items || []).map(saved => <option key={saved.id} value={saved.id}>{saved.symbol} · {EMA_TREND_MODE_LABEL[saved.mode]} · {saved.status}</option>)}</select>
    </div>
    {(replay.error || run.error || runs.error) && <p role="alert">{((replay.error || run.error || runs.error) as Error).message}</p>}
    {result && !result.result && <p role="status">Run {result.status}{result.error ? `: ${result.error}` : ''}</p>}
    {result?.result && <EmaTrendReport report={result.result} />}
  </section>;
}

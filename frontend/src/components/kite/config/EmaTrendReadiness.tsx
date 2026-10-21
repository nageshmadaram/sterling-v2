import React from 'react';
import { api } from '../../../utils/api';
import type { EmaTrendMode } from '../../../types/emaTrend';

interface Report {
  id: string; status: string; symbol: string; mode: string; completed_ms: number;
  checks: { name: string; status: string; detail: unknown }[];
}

export function EmaTrendReadiness({ symbols, modes, revision }: { symbols: string[]; modes: EmaTrendMode[]; revision: number }) {
  const [symbol, setSymbol] = React.useState('');
  const [mode, setMode] = React.useState('');
  const [report, setReport] = React.useState<Report | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const selectedSymbol = symbols.includes(symbol) ? symbol : symbols[0] || '';
  const selectedMode = modes.includes(mode as EmaTrendMode) ? mode : modes[0] || '';
  React.useEffect(() => { setReport(null); setError(''); }, [revision, selectedSymbol, selectedMode]);
  return <div style={{ fontSize: 12 }}>
    <p>Test one name and mode against your account. Reads data only; places no orders.</p>
    <label>Acceptance instrument <select aria-label="Acceptance instrument" value={selectedSymbol} disabled={busy} onChange={event => setSymbol(event.target.value)}>
      {symbols.map(value => <option key={value}>{value}</option>)}
    </select></label>{' '}
    <label>Acceptance profile <select aria-label="Acceptance profile" value={selectedMode} disabled={busy} onChange={event => setMode(event.target.value)}>
      {modes.map(value => <option key={value}>{value}</option>)}
    </select></label>{' '}
    <button type="button" className="sb-tool" disabled={busy || !selectedSymbol || !selectedMode} onClick={async () => {
      setBusy(true); setError(''); setReport(null);
      try {
        setReport(await api.post<Report>('/api/v1/ema-trend/readiness/check', {
          symbol: selectedSymbol, mode: selectedMode, expected_config_revision: revision,
        }));
      } catch (failure) { setError((failure as Error).message); }
      finally { setBusy(false); }
    }}>{busy ? 'Checking broker connection…' : 'Check connected readiness'}</button>
    {error && <p role="alert">Readiness check failed: {error}</p>}
    {report && <div role="status">
      <p><strong>Live readiness: {report.status}</strong> · {report.symbol} · {report.mode} · Observation {new Date(report.completed_ms).toLocaleString()}</p>
      <ul>{report.checks.map((check, index) => <li key={`${check.name}-${index}`}><strong>{check.name.replace(/_/g, ' ')}: {check.status}</strong> — {typeof check.detail === 'string' ? check.detail : JSON.stringify(check.detail)}</li>)}</ul>
      <p>Report: {report.id}. Each check is an observation, not execution authorisation.</p>
    </div>}
  </div>;
}

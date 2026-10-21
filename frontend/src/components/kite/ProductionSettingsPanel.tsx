import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { PanelCard } from './config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';

interface Settings {
  allocation_capital_inr: number | null;
  experiment_id: string;
  dataset_start: string;
  risk_limits: string;
  live_execution: boolean;
}
interface Snapshot {
  settings: Settings;
  runtime: { enabled: boolean };
  safe_mode: { state: string; reason: string };
  posture: {
    ready: boolean;
    missing: string[];
    egress: { registered_ip: string | null; observed_ip: string | null; reason: string };
    binding: { binding_id: string | null; detail: string };
  };
  activation: string;
  risk_scopes?: { strategies: string[]; lanes: string[] };
  checks?: { name: string; passed: boolean | null; detail: string; advisory: boolean }[];
}
const PATH = '/api/v1/ops/production-settings';
const KEY = ['production-settings'];
const inputStyle: React.CSSProperties = { display: 'block', width: '100%', maxWidth: 650, padding: 8, marginTop: 5, background: 'var(--k-bg)', color: 'var(--k-text)', border: '1px solid var(--k-border)', borderRadius: 5 };


/** The four plain limits the form edits. Each is ₹ of exposure. */
export interface SimpleLimits { total: number | null; strategy: number | null; lane: number | null; trade: number | null }

const LIMIT_FIELDS: { key: keyof SimpleLimits; label: string; hint: string }[] = [
  { key: 'total', label: 'Total limit (₹)', hint: 'All strategies together.' },
  { key: 'strategy', label: 'Per strategy (₹)', hint: 'SuperTrend, Snapback, Gamma Unwind each.' },
  { key: 'lane', label: 'Per mode (₹)', hint: 'Each mode: scalping, intraday, overnight, swing.' },
  { key: 'trade', label: 'Per trade (₹)', hint: 'One position.' },
];

const positive = (v: unknown): number | null => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
};

/** Read the plain limits back out of a saved hierarchy. Uses the largest value per level. */
export function parseSimpleLimits(raw: string): SimpleLimits {
  const empty: SimpleLimits = { total: null, strategy: null, lane: null, trade: null };
  if (!raw.trim()) return empty;
  try {
    const blob = JSON.parse(raw) as Record<string, Record<string, unknown>>;
    const max = (level: string) => {
      const values = Object.values(blob?.[level] ?? {}).map(positive).filter((v): v is number => v !== null);
      return values.length ? Math.max(...values) : null;
    };
    return { total: positive(blob?.global?.['']), strategy: max('strategy'), lane: max('mode'), trade: max('position') };
  } catch {
    return empty;
  }
}

/** Build the hierarchy JSON. Empty when any field is missing: a partial set would leave lanes uncapped. */
export function buildRiskLimits(limits: SimpleLimits, scopes: { strategies: string[]; lanes: string[] }): string {
  const { total, strategy, lane, trade } = limits;
  if (total == null || strategy == null || lane == null || trade == null || !scopes.lanes.length) return '';
  const each = (keys: string[], value: number) => Object.fromEntries(keys.map((k) => [k, value]));
  return JSON.stringify({
    global: { '': total },
    strategy: each(scopes.strategies, strategy),
    mode: each(scopes.lanes, lane),
    position: each(scopes.lanes, trade),
  });
}

export function limitsProblem(limits: SimpleLimits): string | null {
  const { total, strategy, lane, trade } = limits;
  if ([total, strategy, lane, trade].some((v) => v == null)) return 'Fill all four limits.';
  if (trade! > lane! || lane! > strategy! || strategy! > total!) return 'Each limit must be at most the one above it.';
  return null;
}

export function ProductionSettingsPanel() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: KEY, queryFn: () => api.get<Snapshot>(PATH), retry: false });
  const [draft, setDraft] = useState<Settings | null>(null);
  const [confirm, setConfirm] = useState('');
  const [whitelisted, setWhitelisted] = useState(false);
  const [saved, setSaved] = useState(false);
  const [limitsDraft, setLimitsDraft] = useState<SimpleLimits | null>(null);
  const save = useMutation({
    mutationFn: (settings: Settings) => api.put(PATH, { settings, confirm }),
    onSuccess: () => { setDraft(null); setLimitsDraft(null); setConfirm(''); setSaved(true); void qc.invalidateQueries({ queryKey: KEY }); },
  });
  const acceptIp = useMutation({
    mutationFn: (ip: string) => api.post('/api/v1/ops/live-posture/egress/accept', { ip, confirm: 'ACCEPT IP' }),
    onSuccess: () => { setWhitelisted(false); void qc.invalidateQueries({ queryKey: KEY }); void qc.invalidateQueries({ queryKey: ['ops-live-status'] }); },
  });
  const cfg = draft ?? query.data?.settings;
  const dirty = draft !== null;
  useUnsavedDraftGuard('production-settings', dirty);
  if (query.isError) return <PanelCard><h3>Production setup</h3><p role="alert">Production setup is unavailable. Administrator access is required.</p><button onClick={() => void query.refetch()}>Retry production setup</button></PanelCard>;
  if (!cfg || !query.data) return <PanelCard><p>Loading production setup…</p></PanelCard>;
  const data = query.data;
  const update = (change: Partial<Settings>) => { setDraft({ ...cfg, ...change }); setSaved(false); };
  const scopes = data.risk_scopes ?? { strategies: [], lanes: [] };
  const limits = limitsDraft ?? parseSimpleLimits(cfg.risk_limits);
  const limitsError = limitsDraft ? limitsProblem(limitsDraft) : null;
  const setLimit = (key: keyof SimpleLimits, value: string) => {
    const next = { ...limits, [key]: value === '' ? null : positive(value) };
    setLimitsDraft(next);
    update({ risk_limits: buildRiskLimits(next, scopes) });
  };
  const valid = !limitsError && (cfg.allocation_capital_inr === null || (Number.isFinite(cfg.allocation_capital_inr) && cfg.allocation_capital_inr > 0))
    && (!cfg.live_execution || (confirm === 'ENABLE LIVE ENTRIES' && data.posture.ready && data.safe_mode.state === 'NORMAL' && !!cfg.allocation_capital_inr && !!cfg.risk_limits.trim()));
  return <PanelCard>
    <h3>Production setup</h3>
    <p role="status">Running entry switch: {data.runtime.enabled ? 'enabled' : 'disabled'}. Safe mode: {data.safe_mode.state === 'NORMAL' ? 'off' : 'on'}.</p>
    <p>These settings take effect after a backend restart. Saving does not enable strategy automation or bypass order checks.</p>
    {data.posture.missing.length > 0 && <p role="alert">Live setup requires: {data.posture.missing.join(', ')}.</p>}
    <div style={{ display: 'grid', gap: 16 }}>
      <label>Approved live capital (₹)<input aria-label="Approved live capital (₹)" style={inputStyle} type="number" min="0.01" step="0.01" value={cfg.allocation_capital_inr ?? ''} onChange={e => update({ allocation_capital_inr: e.target.value === '' ? null : Number(e.target.value) })} /></label>
      <fieldset style={{ border: '1px solid var(--k-border)', borderRadius: 6, padding: 12 }}>
        <legend>Risk limits</legend>
        <p style={{ marginTop: 0 }}>Maximum exposure in ₹, largest first.</p>
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
          {LIMIT_FIELDS.map(f => <label key={f.key}>{f.label}
            <input aria-label={f.label} style={inputStyle} type="number" min="1" step="1" value={limits[f.key] ?? ''} onChange={e => setLimit(f.key, e.target.value)} />
            <span style={{ fontSize: 11, color: 'var(--k-dim)' }}>{f.hint}</span>
          </label>)}
        </div>
        {limitsError && <p role="alert">{limitsError}</p>}
        <p style={{ marginBottom: 0, fontSize: 12 }}>Daily loss: Shared risk settings below. Lots per trade: each strategy's own settings.</p>
      </fieldset>
      <details>
        <summary>Advanced</summary>
        <div style={{ display: 'grid', gap: 12, marginTop: 10 }}>
          <label>Risk limits (JSON)<textarea style={inputStyle} rows={6} value={cfg.risk_limits} onChange={e => { setLimitsDraft(null); update({ risk_limits: e.target.value }); }} /></label>
          <label>Experiment ID<input style={inputStyle} value={cfg.experiment_id} onChange={e => update({ experiment_id: e.target.value })} /></label>
          <label>Dataset start with timezone<input style={inputStyle} placeholder="YYYY-MM-DDT00:00:00+05:30" value={cfg.dataset_start} onChange={e => update({ dataset_start: e.target.value })} /></label>
        </div>
      </details>
      <label><input type="checkbox" checked={cfg.live_execution} onChange={e => update({ live_execution: e.target.checked })} /> Request live entries after restart</label>
      {cfg.live_execution && <label>Type ENABLE LIVE ENTRIES<input style={inputStyle} value={confirm} onChange={e => setConfirm(e.target.value)} /></label>}
    </div>
    {save.isError && <p role="alert">{save.error.message}</p>}
    <p><button disabled={!dirty || !valid || save.isPending} onClick={() => save.mutate(cfg)}>{save.isPending ? 'Saving…' : 'Save production settings'}</button> <button disabled={!dirty || save.isPending} onClick={() => { setDraft(null); setLimitsDraft(null); setConfirm(''); }}>Discard production changes</button></p>
    {saved && <p role="status">Production settings saved. Restart the backend to apply them; then recheck readiness.</p>}
    <h4>Broker connection requirements</h4>
    <p>Registered outbound IP: {data.posture.egress.registered_ip ?? 'unknown'}. Observed IP: {data.posture.egress.observed_ip ?? 'unknown'}.</p>
    <p>{data.posture.egress.reason}</p>
    <label><input type="checkbox" checked={whitelisted} onChange={e => setWhitelisted(e.target.checked)} /> I have added the observed IP to my Zerodha API whitelist.</label>
    <p><button disabled={!whitelisted || !data.posture.egress.observed_ip || acceptIp.isPending} onClick={() => acceptIp.mutate(data.posture.egress.observed_ip!)}>Verify and accept observed IP</button></p>
    {acceptIp.isError && <p role="alert">{acceptIp.error.message}</p>}
    <p>Account binding: {data.posture.binding.binding_id ?? 'none'}. {data.posture.binding.detail}</p>
    <h4>Readiness checks</h4>
    {!data.checks ? <p>Readiness checks unavailable.</p> : <ul>{data.checks.filter(check => check.passed !== true).map(check => <li key={check.name}>
      <strong>{check.name.replace(/_/g, ' ')}</strong> ({check.advisory ? 'advisory' : check.passed === null ? 'unknown' : 'blocked'}): {check.detail}
    </li>)}</ul>}
    <button onClick={() => void query.refetch()}>Refresh production status</button>
  </PanelCard>;
}

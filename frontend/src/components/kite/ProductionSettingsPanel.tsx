import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar } from './config/ConfigPrimitives';
import { BORDER, Field, MUTED, Section, TEXT, inputStyle } from './kiteSettingsPrimitives';
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

const wideInput: React.CSSProperties = { ...inputStyle, width: 140, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
const textInput: React.CSSProperties = { ...inputStyle, width: 260 };
const button = (primary: boolean, enabled: boolean): React.CSSProperties => ({
  padding: '6px 14px', fontSize: 11.5, fontWeight: primary ? 700 : 600, fontFamily: 'inherit', borderRadius: 6,
  border: `1px solid ${primary && enabled ? 'var(--k-brand)' : BORDER}`, background: 'transparent',
  color: primary && enabled ? 'var(--k-brand)' : MUTED, cursor: enabled ? 'pointer' : 'default',
});
const chip = (tone: 'ok' | 'warn' | 'err'): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
  border: `1px solid ${tone === 'ok' ? 'var(--k-green)' : tone === 'warn' ? 'var(--k-amber)' : 'var(--k-red)'}`,
  color: tone === 'ok' ? 'var(--k-green)' : tone === 'warn' ? 'var(--k-amber)' : 'var(--k-red)',
});
const alertStyle: React.CSSProperties = { color: 'var(--k-red)', fontSize: 11.5, margin: '6px 0' };
const okStyle: React.CSSProperties = { color: 'var(--k-green)', fontSize: 11.5 };
const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 0', borderTop: `1px solid ${BORDER}`, marginTop: 4 };
const subHead: React.CSSProperties = { color: TEXT, fontSize: 12, fontWeight: 700, margin: '14px 0 4px' };

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
  if (query.isError) return <PanelCard><Section title="Production setup" description="Capital, risk limits and the live entry switch." summary="Unavailable" defaultOpen persistKey="production-setup"><p role="alert" style={alertStyle}>Production setup is unavailable. Administrator access is required.</p><div style={row}><button type="button" style={button(true, true)} onClick={() => void query.refetch()}>Retry production setup</button></div></Section></PanelCard>;
  if (!cfg || !query.data) return <PanelCard><p style={{ color: MUTED, fontSize: 12, padding: 12 }}>Loading production setup…</p></PanelCard>;
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
  const blocking = (data.checks ?? []).filter(check => check.passed !== true && !check.advisory);
  const advisory = (data.checks ?? []).filter(check => check.passed !== true && check.advisory);
  const liveOn = data.runtime.enabled;
  const safeOn = data.safe_mode.state !== 'NORMAL';
  return <PanelCard><Section title="Production setup" description="Capital, risk limits and the live entry switch. Applied after a backend restart."
    summary={`${liveOn ? 'Live on' : 'Live off'} · ${safeOn ? 'Safe mode on' : 'Safe mode off'}`} defaultOpen persistKey="production-setup">
    <div role="status" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '8px 0' }}>
      <span style={chip(liveOn ? 'ok' : 'warn')}>Running entry switch: {liveOn ? 'enabled' : 'disabled'}</span>
      <span style={chip(safeOn ? 'err' : 'ok')}>Safe mode: {safeOn ? 'on' : 'off'}</span>
      <span style={chip(blocking.length ? 'err' : 'ok')}>{blocking.length ? `${blocking.length} blocking check${blocking.length > 1 ? 's' : ''}` : 'Checks clear'}</span>
    </div>
    {data.posture.missing.length > 0 && <p role="alert" style={alertStyle}>Live setup requires: {data.posture.missing.join(', ')}.</p>}

    <Field label="Approved live capital (₹)" hint="Total capital Sterling may use.">
      <input aria-label="Approved live capital (₹)" style={wideInput} type="number" min="0.01" step="0.01" value={cfg.allocation_capital_inr ?? ''} onChange={e => update({ allocation_capital_inr: e.target.value === '' ? null : Number(e.target.value) })} />
    </Field>
    <div style={subHead}>Risk limits</div>
    <div style={{ color: MUTED, fontSize: 10.5, marginBottom: 2 }}>Maximum exposure in ₹, largest first.</div>
    {LIMIT_FIELDS.map(f => <Field key={f.key} label={f.label} hint={f.hint}>
      <input aria-label={f.label} style={wideInput} type="number" min="1" step="1" value={limits[f.key] ?? ''} onChange={e => setLimit(f.key, e.target.value)} />
    </Field>)}
    {limitsError && <p role="alert" style={alertStyle}>{limitsError}</p>}
    <ConfigNote>Daily loss: Shared risk settings below. Lots per trade: each strategy's own settings.</ConfigNote>

    <Field label="Request live entries after restart" hint="Real money. Needs safe mode off and every blocking check clear.">
      <input aria-label="Request live entries after restart" type="checkbox" checked={cfg.live_execution} onChange={e => update({ live_execution: e.target.checked })} />
    </Field>
    {cfg.live_execution && <Field label="Type ENABLE LIVE ENTRIES" hint="Confirms you intend to trade real money.">
      <input aria-label="Type ENABLE LIVE ENTRIES" style={textInput} value={confirm} onChange={e => setConfirm(e.target.value)} />
    </Field>}

    <AdvancedSection count={3}>
      <Field label="Risk limits (JSON)" hint="Edited by the four fields above." wide>
        <textarea aria-label="Risk limits (JSON)" style={{ ...inputStyle, width: '100%', height: 'auto', padding: 8, fontFamily: 'ui-monospace, monospace', fontSize: 11 }} rows={5} value={cfg.risk_limits} onChange={e => { setLimitsDraft(null); update({ risk_limits: e.target.value }); }} />
      </Field>
      <Field label="Experiment ID" hint="Snapback research recorder only.">
        <input aria-label="Experiment ID" style={textInput} value={cfg.experiment_id} onChange={e => update({ experiment_id: e.target.value })} />
      </Field>
      <Field label="Dataset start with timezone" hint="Snapback research recorder only.">
        <input aria-label="Dataset start with timezone" style={textInput} placeholder="YYYY-MM-DDT00:00:00+05:30" value={cfg.dataset_start} onChange={e => update({ dataset_start: e.target.value })} />
      </Field>
    </AdvancedSection>

    {save.isError && <p role="alert" style={alertStyle}>{save.error.message}</p>}
    {saved && !dirty && <p role="status" style={okStyle}>Production settings saved. Restart the backend to apply them; then recheck readiness.</p>}
    <SettingsDraftBar dirty={dirty} saving={save.isPending} applyDisabled={!valid}
      applyTitle={valid ? undefined : (limitsError || 'Complete the required fields first')}
      onApply={() => save.mutate(cfg)}
      onDiscard={() => { setDraft(null); setLimitsDraft(null); setConfirm(''); }} />

    <div style={subHead}>Broker connection</div>
    <Field label="Outbound IP" hint={data.posture.egress.reason}>
      <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{data.posture.egress.observed_ip ?? 'unknown'}{data.posture.egress.registered_ip && data.posture.egress.registered_ip !== data.posture.egress.observed_ip ? ` (registered ${data.posture.egress.registered_ip})` : ''}</span>
    </Field>
    <Field label="Account binding" hint={data.posture.binding.detail}>
      <span style={{ fontSize: 12 }}>{data.posture.binding.binding_id ?? 'none'}</span>
    </Field>
    <div style={row}>
      <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 11.5, color: TEXT }}><input type="checkbox" checked={whitelisted} onChange={e => setWhitelisted(e.target.checked)} /> I have added the observed IP to my Zerodha API whitelist.</label>
      <button type="button" style={button(true, whitelisted && !!data.posture.egress.observed_ip && !acceptIp.isPending)} disabled={!whitelisted || !data.posture.egress.observed_ip || acceptIp.isPending} onClick={() => acceptIp.mutate(data.posture.egress.observed_ip!)}>Verify and accept observed IP</button>
    </div>
    {acceptIp.isError && <p role="alert" style={alertStyle}>{acceptIp.error.message}</p>}

    <div style={subHead}>Readiness checks</div>
    {!data.checks ? <p style={{ color: MUTED, fontSize: 11.5 }}>Readiness checks unavailable.</p> : <>
      {blocking.length === 0 ? <p style={okStyle}>No blocking checks.</p> :
        <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 11.5, color: TEXT }}>{blocking.map(check => <li key={check.name} style={{ margin: '3px 0' }}>
          <strong>{check.name.replace(/_/g, ' ')}</strong> <span style={{ color: check.passed === null ? 'var(--k-amber)' : 'var(--k-red)' }}>({check.passed === null ? 'unknown' : 'blocked'})</span>: {check.detail}
        </li>)}</ul>}
      {advisory.length > 0 && <AdvancedSection count={advisory.length}>
        <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 11, color: MUTED }}>{advisory.map(check => <li key={check.name} style={{ margin: '3px 0' }}>
          <strong>{check.name.replace(/_/g, ' ')}</strong> (advisory): {check.detail}
        </li>)}</ul>
      </AdvancedSection>}
    </>}
    <div style={row}><button type="button" style={button(false, true)} onClick={() => void query.refetch()}>Refresh production status</button></div>
  </Section></PanelCard>;
}

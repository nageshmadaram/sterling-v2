import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { ConfigNote, PanelCard, SettingsDraftBar } from './config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';
import { Field, Section, Switch, inputStyle, MUTED, TEXT } from './kiteSettingsPrimitives';

type Strategy = 'snapback' | 'gamma_unwind' | 'supertrend';
export interface SharedRiskConfig {
  revision: number; enabled: boolean; strategies: Strategy[];
  allocation_inr: number | null; max_daily_loss_inr: number | null; per_trade_risk_inr: number | null;
}
const PATH = '/api/v1/config/shared-risk';
const KEY = ['shared-risk-settings'];
const strategies: [Strategy, string][] = [['snapback', 'Snapback'], ['gamma_unwind', 'Gamma-Unwind'], ['supertrend', 'SuperTrend']];
const fields: [keyof Pick<SharedRiskConfig, 'allocation_inr' | 'max_daily_loss_inr' | 'per_trade_risk_inr'>, string, string][] = [
  ['allocation_inr', 'Allocation cap (₹)', 'Maximum account capital deployed or reserved when a selected strategy opens a trade.'],
  ['max_daily_loss_inr', 'Maximum daily loss (₹)', 'Stops selected strategies after the account’s realised loss reaches this amount.'],
  ['per_trade_risk_inr', 'Per-trade risk cap (₹)', 'Maximum premium or notional committed by a new trade. A stop price does not reduce this cap.'],
];

export function SharedRiskSettings() {
  const query = useQuery({ queryKey: KEY, queryFn: () => api.get<SharedRiskConfig>(PATH) });
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState<SharedRiskConfig | null>(null);
  const save = useMutation({ mutationFn: (body: SharedRiskConfig) => api.put<SharedRiskConfig>(PATH, body), onSuccess: (data) => { qc.setQueryData(KEY, data); setDraft(null); } });
  const cfg = draft ?? query.data;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(query.data);
  useUnsavedDraftGuard('shared-risk', dirty);
  if (query.isError) return <PanelCard><p role="alert">Shared risk settings could not be loaded. Retry before changing limits.</p><button onClick={() => void query.refetch()}>Retry</button></PanelCard>;
  if (!cfg) return <PanelCard><p style={{ color: MUTED }}>Loading shared risk settings…</p></PanelCard>;
  const update = (change: Partial<SharedRiskConfig>) => setDraft({ ...cfg, ...change });
  const positive = fields.every(([key]) => cfg[key] == null || (Number.isFinite(cfg[key]) && cfg[key]! > 0));
  const bounded = cfg.allocation_inr == null || cfg.per_trade_risk_inr == null || cfg.per_trade_risk_inr <= cfg.allocation_inr;
  const active = !cfg.enabled || (cfg.strategies.length > 0 && fields.some(([key]) => cfg[key] != null));
  const valid = positive && bounded && active;
  return <PanelCard><Section title="Shared risk settings" description="Override strategy-specific risk settings for the strategies you select." summary={cfg.enabled ? `On · ${cfg.strategies.length} strategies` : 'Off'} defaultOpen persistKey="shared-risk">
    <Field label="Shared overrides" hint="Saving these settings does not enable trading."><Switch checked={cfg.enabled} label={cfg.enabled ? 'On' : 'Off'} onChange={() => update({ enabled: !cfg.enabled })} /></Field>
    <fieldset style={{ border: 0, padding: '8px 0', margin: 0, color: TEXT }}><legend>Apply to strategies</legend>{strategies.map(([id, label]) => <label key={id} style={{ display: 'inline-flex', gap: 6, marginRight: 18, padding: '8px 0' }}><input type="checkbox" checked={cfg.strategies.includes(id)} onChange={() => update({ strategies: cfg.strategies.includes(id) ? cfg.strategies.filter((s) => s !== id) : [...cfg.strategies, id] })} />{label}</label>)}</fieldset>
    {fields.map(([key, label, hint]) => <Field key={key} label={label} hint={hint}><input aria-label={label} type="number" min="0.01" step="0.01" style={inputStyle} value={cfg[key] ?? ''} onChange={(event) => update({ [key]: event.target.value === '' ? null : Number(event.target.value) })} placeholder="Keep strategy setting" /></Field>)}
    <ConfigNote>Filled values override the selected strategies’ own settings, or apply a new limit where none exists. Blank values keep strategy settings. Unselected strategies are unchanged. Allocation and daily loss measure the account as a whole. Account-wide halts, exits and broker protection still apply.</ConfigNote>
    {!valid && <p role="alert">Choose a strategy and at least one positive limit. Per-trade risk cannot exceed allocation.</p>}
    {save.isError && <p role="alert">{save.error instanceof Error ? save.error.message : 'Risk settings could not be saved.'}</p>}
    {save.isSuccess && !draft && <p role="status" style={{ color: 'var(--k-green)', fontSize: 11.5 }}>Shared risk settings saved</p>}
    <SettingsDraftBar dirty={dirty} saving={save.isPending} applyDisabled={!valid}
      applyTitle={valid ? undefined : 'Choose a strategy and at least one positive limit'}
      onApply={() => save.mutate(cfg)} onDiscard={() => setDraft(null)} />
  </Section></PanelCard>;
}

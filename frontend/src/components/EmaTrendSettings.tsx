import { ModeAvailability } from './kite/config/ModeAvailability';
import type { TradeMode } from './kite/board/boardTypes';
import React from 'react';
import { useEmaTrendConfig, useUpdateEmaTrend, useEmaTrendLiveReadiness } from '../hooks/useEmaTrend';
import { EMA_TREND_MODE_LABEL, type EmaTrendConfig, type EmaTrendMode } from '../types/emaTrend';
import { Section, Field, NumberField, Switch } from './kite/kiteSettingsPrimitives';
import { ConfigNote, PanelCard, SettingsDraftBar } from './kite/config/ConfigPrimitives';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { api } from '../utils/api';
import { EmaTrendInstrumentPicker } from './kite/config/EmaTrendInstrumentPicker';
import { EmaTrendReadiness } from './kite/config/EmaTrendReadiness';


const LIMITS: Array<{ key: keyof EmaTrendConfig; label: string; min: number; max: number; step: number }> = [
  { key: 'allocation_inr', label: 'Paper allocation (₹)', min: 0, max: 100000000, step: 1000 },
  { key: 'risk_fraction', label: 'Risk fraction per campaign', min: .0001, max: .005, step: .0001 },
  { key: 'strategy_risk_fraction', label: 'Total strategy risk fraction', min: .0001, max: .0075, step: .0001 },
  { key: 'daily_loss_fraction', label: 'Daily loss fraction', min: .0001, max: .01, step: .0001 },
  { key: 'drawdown_fraction', label: 'Drawdown fraction', min: .0001, max: .05, step: .0001 },
  { key: 'max_campaigns', label: 'Maximum campaigns', min: 1, max: 3, step: 1 },
  { key: 'max_sector_campaigns', label: 'Maximum campaigns per sector', min: 1, max: 2, step: 1 },
  { key: 'fee_bps', label: 'Estimated fees per leg (bps)', min: 0, max: 100, step: 1 },
  { key: 'brokerage_per_order', label: 'Estimated brokerage per order (₹)', min: 0, max: 1000, step: 1 },
  { key: 'slippage_bps', label: 'Estimated slippage per leg (bps)', min: 0, max: 100, step: 1 },
  { key: 'gap_reserve_atr', label: 'Gap reserve (ATR)', min: 0, max: 2, step: .05 },
];
export function EmaTrendSettings() {
  const query = useEmaTrendConfig();
  const save = useUpdateEmaTrend();
  const live = useEmaTrendLiveReadiness();
  // Hold the revision alongside the draft: background refresh must never rebase a stale draft silently.
  const [draft, setDraft] = React.useState<{ config: EmaTrendConfig; revision: number } | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);
  const [symbolsText, setSymbolsText] = React.useState<string | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const [exportError, setExportError] = React.useState('');
  const exportRecords = async () => {
    setExporting(true); setExportError('');
    try {
      const data = await api.get('/api/v1/ema-trend/export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url; link.download = `ema-trend-paper-${new Date().toISOString().slice(0, 10)}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setExportError((error as Error).message); }
    finally { setExporting(false); }
  };
  const server = query.data;
  const cfg = draft?.config ?? server?.config;
  const changes = React.useMemo(() => {
    const result: Partial<EmaTrendConfig> = {};
    if (draft && server) for (const key of Object.keys(draft.config) as Array<keyof EmaTrendConfig>) {
      if (JSON.stringify(draft.config[key]) !== JSON.stringify(server.config[key])) {
        (result as Record<string, unknown>)[key] = draft.config[key];
      }
    }
    return result;
  }, [draft, server]);
  const dirty = Boolean(draft) && Object.keys(changes).length > 0;
  useUnsavedDraftGuard('emaTrend', dirty);
  const patch = (next: Partial<EmaTrendConfig>) => {
    if (!server) return;
    setDraft(previous => ({ revision: previous?.revision ?? server.revision,
      config: { ...(previous?.config ?? server.config), ...next } }));
  };
  if (query.isError) return <PanelCard><p role="alert">EMA Trend settings unavailable: {(query.error as Error).message}</p><button onClick={() => query.refetch()}>Retry</button></PanelCard>;
  if (!cfg || !server) return <PanelCard>Loading EMA Trend settings…</PanelCard>;
  const conflict = draft !== null && draft.revision !== server.revision;
  const modes = Object.keys(server.profiles || server.strategy.profiles) as EmaTrendMode[];
  return <>
    <SettingsDraftBar dirty={dirty} saving={save.isPending} applyDisabled={conflict} hasDraft={draft !== null} resetConfirm={resetConfirm}
      onApply={() => { if (draft && dirty) save.mutate({ expected_revision: draft.revision, changes }, { onSuccess: () => { setDraft(null); setSymbolsText(null); } }); }}
      onDiscard={() => { setDraft(null); setSymbolsText(null); save.reset(); }}
      onReset={() => { if (!resetConfirm) { setResetConfirm(true); return; }
        setDraft({ config: { ...server.defaults }, revision: server.revision }); setSymbolsText(null); setResetConfirm(false); }} />
    {save.error && <p role="alert">Save failed: {(save.error as Error).message}. Your draft is retained.</p>}
    {conflict && <p role="alert">Settings changed elsewhere. Discard to load the latest; your changes were not saved.</p>}
    <button type="button" className="sb-tool" onClick={() => { setDraft({ config: { ...server.defaults }, revision: server.revision }); setSymbolsText(null); }}>Stage server defaults</button>
    <EnginePowerHeader name={server.strategy.name} tagline="Buys only · stocks, futures, options · EMA 10/21 · DEMA 50 · SuperTrend (10,3)"
      on={cfg.enabled} liveOn={server.config.enabled} busy={save.isPending} onToggle={() => patch({ enabled: !cfg.enabled })}
      runningNote="Scanning on. Open trades stay managed if you turn it off."
      offNote="Scanning off. Practice trades only, no real orders." />
    <Section title="Execution and evidence" summary="Paper only" description="Practice trades only. No real orders." defaultOpen persistKey="ema-trend-execution">
      <Field label="Automatic paper entry" hint="Separate from scanning. Safety checks still apply.">
        <Switch label="EMA Trend automatic paper entry" checked={cfg.auto_execute} onChange={() => patch({ auto_execute: !cfg.auto_execute })} />
      </Field>
      <ConfigNote>Execution: {cfg.execution_mode}. Revision {server.revision}. {server.paused ? 'New exposure is paused.' : 'New exposure is not paused.'} Evidence remains inconclusive; profits are not guaranteed.</ConfigNote>
      <button type="button" className="sb-tool" disabled={live.isFetching}
        onClick={() => { void live.refetch(); }}>{live.isFetching ? 'Checking Kite account…' : 'Check Kite live readiness'}</button>
      {live.error && <p role="alert">Kite live readiness check failed: {(live.error as Error).message}</p>}
      {live.data && <div role="status" aria-label="EMA live readiness">
        <strong>{live.data.status.replace(/_/g, ' ')}</strong>
        <p>Live Kite account: {live.data.account_id ?? 'Not bound'} · Broker orders: not enabled.</p>
        {Object.entries(live.data.checks).map(([key, ready]) =>
          <p key={key}>{key.replace(/_/g, ' ')}: {ready ? 'Verified' : 'Not verified'}</p>)}
        {Object.entries(live.data.modes).map(([mode, details]) => details &&
          <p key={mode}><strong>{EMA_TREND_MODE_LABEL[mode as EmaTrendMode]} ({details.product})</strong>:
            {' '}{details.blockers.join(' · ').replace(/_/g, ' ')}</p>)}
        <p>Being logged in to Kite is not enough. These checks must pass first.</p>
      </div>}
      <details style={{ fontSize: 11, color: 'var(--k-text-muted)' }}>
        <summary style={{ cursor: 'pointer' }}>
          Live trading blocked · {server.blockers?.length || 0} certification checks
        </summary>
        <p>These checks block real orders, not scanning.</p>
        {(server.blockers || []).map(reason => <p key={reason} style={{ color: 'var(--k-amber)' }}>
          {reason.replace(/_/g, ' ')}
        </p>)}
      </details>
      <ConfigNote>Version {server.strategy.version} · rule {server.strategy.rule_hash}</ConfigNote>
      <EmaTrendReadiness symbols={server.config.symbols} modes={server.config.modes} revision={server.revision} />
      <button type="button" className="sb-tool" disabled={exporting} onClick={exportRecords}>{exporting ? 'Exporting…' : 'Export saved paper records'}</button>
      {exportError && <p role="alert">Export failed: {exportError}</p>}
      <ConfigNote>Exports settings, trades and records. No passwords. Not a Zerodha backup.</ConfigNote>
    </Section>
    <Section title="Scan universe" summary={`${cfg.scan_indices?.length || 0} indices · ${cfg.scan_stocks_enabled === false ? 0 : cfg.symbols.length} instruments · ${cfg.modes.length} profiles`}
      description="Pick indices, F&O stocks, or exact contracts." defaultOpen persistKey="ema-trend-universe">
      <EmaTrendInstrumentPicker symbols={cfg.symbols} mode={cfg.modes[0]}
        indices={cfg.scan_indices || []} stockScanEnabled={cfg.scan_stocks_enabled !== false}
        onIndicesChange={scan_indices => patch({ scan_indices })}
        onStockScanEnabledChange={scan_stocks_enabled => patch({ scan_stocks_enabled })}
        onChange={symbols => { setSymbolsText(null); patch({ symbols }); }} />
      <details style={{ marginTop: 8 }}>
        <summary style={{ cursor: 'pointer', fontSize: 11 }}>Advanced: edit exact exchange symbols</summary>
        <Field label="Exchange-qualified instruments" hint="Advanced. Checked against Zerodha during scans.">
          <textarea aria-label="EMA Trend instruments" value={symbolsText ?? cfg.symbols.join(', ')} rows={2}
            style={{ width: '100%', background: 'var(--k-bg)', color: 'var(--k-text)', border: '1px solid var(--k-border)' }}
            onChange={event => { setSymbolsText(event.target.value); patch({ symbols: event.target.value.split(/[,\n]/).map(v => v.trim().toUpperCase()).filter(Boolean) }); }} />
        </Field>
      </details>
      <ConfigNote>Changes wait for Apply. Indices are watch only. No orders are sent.</ConfigNote>
      <Field label="Enabled profiles" hint="Pick at least one mode. One name trades in one mode at a time.">
        <ModeAvailability available={modes.map(m => m as TradeMode)} active={cfg.modes.map(m => m as TradeMode)} note="Tick the modes to scan below. Practice trades only." />
        <div role="group" aria-label="EMA Trend profiles">{modes.map(mode => <label key={mode} style={{ display: 'inline-flex', gap: 6, marginRight: 14 }}>
          <input type="checkbox" checked={cfg.modes.includes(mode)} disabled={cfg.modes.length === 1 && cfg.modes.includes(mode)}
            onChange={() => patch({ modes: cfg.modes.includes(mode) ? cfg.modes.filter(m => m !== mode) : [...cfg.modes, mode] })} />{EMA_TREND_MODE_LABEL[mode]}
        </label>)}</div>
      </Field>
      {modes.map(mode => { const p = (server.profiles || server.strategy.profiles)[mode]; return <ConfigNote key={mode}>
        <strong>{EMA_TREND_MODE_LABEL[mode]}</strong>: {p.signal_minutes === 1440 ? 'Daily' : `${p.signal_minutes}m`} signals / {p.context_minutes === 10080 ? 'completed weekly' : `${p.context_minutes}m`} context · {mode === 'swing' ? 'CNC equities / NRML derivatives' : p.product} · {p.require_green ? 'green ST required' : 'ST context only'} · {p.force_close ? `close by ${p.force_close} IST` : 'condition-based holding'}{p.max_hold_bars ? ` · maximum ${p.max_hold_bars} bars` : ''}.
      </ConfigNote>; })}
    </Section>
    <Section title="Risk and estimated costs" summary={`₹${cfg.allocation_inr} paper allocation`} description="Fractions are decimal shares of the paper allocation: 0.0025 = 0.25%. Each initial position needs at least four shares or four complete derivative lots." defaultOpen persistKey="ema-trend-risk">
      {LIMITS.map(field => <NumberField key={field.key} label={field.label} value={Number(cfg[field.key])} min={field.min} max={field.max} step={field.step}
          onChange={value => { if (value !== null && Number.isFinite(value)) patch({ [field.key]: value }); }} />)}
      <ConfigNote>Fee schedule: {cfg.fee_schedule}. These are paper estimates, not verified broker charges. Initial campaign risk stays fixed after profits or add-backs.</ConfigNote>
    </Section>
    <Section title="Frozen strategy rules" summary="10 / 21 / 50 · ST(10,3)" description="Fixed values, shown for reference." persistKey="ema-trend-rules">
      <ConfigNote>Entry: EMA 10 crosses above EMA 21, then a retest or a breakout from a 3–5 candle pause. Stop below the last swing low.</ConfigNote>
      <ConfigNote>Exit: sell a quarter at 4R, then a part on each second close below each EMA. A lower high after a bearish cross closes the rest. Adds need fresh strength; never averages down.</ConfigNote>
      <ConfigNote>Uses EMA 10, EMA 21, DEMA 50 and SuperTrend (10,3). Intraday, Scalp and Ultra scalp are still being tested.</ConfigNote>
    </Section>
  </>;
}

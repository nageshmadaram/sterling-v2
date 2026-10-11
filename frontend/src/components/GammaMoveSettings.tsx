import React from 'react';
import {
  useGammaMoveConfig, useUpdateGammaMove,
  type DirectionMode, type GammaMoveConfig, type StopMode,
} from '../hooks/useGammaMove';
import { ChoiceRow, Field, NumberField, Section, Switch, DIM } from './kite/kiteSettingsPrimitives';
import { AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar } from './kite/config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { InstrumentsGroup } from './kite/config/ScanSettings';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';
import { notifyOrder } from '../store/useKiteNotifications';

const STOP_MODE_OPTIONS: Array<{ value: StopMode; label: string; hint: string }> = [
  { value: 'both', label: 'Broker + monitor', hint: 'For intraday and post-T1 premium stops: broker GTT plus Sterling tick monitor.' },
  { value: 'broker', label: 'Broker only', hint: 'Use the broker GTT where Gamma-Unwind defines a premium stop.' },
  { value: 'monitor', label: 'Monitor only', hint: 'No broker-side stop; Sterling must stay running. Live entry is refused in this mode.' },
];
const DIRECTION_OPTIONS: Array<{ value: DirectionMode; label: string; hint: string }> = [
  { value: 'any', label: 'Any', hint: 'Client default: VWAP alignment OR 30-minute drift alignment.' },
  { value: 'all', label: 'All', hint: 'Require both direction checks.' },
];

function TimeField({ label, value, onChange, hint }: {
  label: string; value: string; onChange: (value: string) => void; hint: string;
}) {
  return <Field label={label} hint={hint}><input type="time" value={value}
    onChange={(e) => onChange(e.target.value)}
    style={{ background: 'transparent', border: '1px solid var(--k-border)', borderRadius: 6,
      color: 'var(--k-text)', padding: '4px 8px', fontSize: 12 }} /></Field>;
}
function timeAt(values: string[], index: number, fallback: string) {
  return values[index] ?? fallback;
}
function setTime(values: string[], index: number, value: string) {
  const next = [...values]; next[index] = value; return next;
}

export function GammaMoveSettings() {
  const { data, isLoading } = useGammaMoveConfig();
  const setCfg = useUpdateGammaMove();
  const server = data?.config;
  const defaults = data?.defaults;
  const strategy = data?.strategy;
  const eligible = data?.vocabularies?.scan_stocks ?? [];
  const effective = data?.client_effective;
  const [draft, setDraft] = React.useState<GammaMoveConfig | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);
  const cfg = draft ?? server ?? null;
  const dirty = draft != null && server != null
    && (Object.keys(draft) as (keyof GammaMoveConfig)[])
      .some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));
  useUnsavedDraftGuard('gammaMove', dirty);
  const patch = React.useCallback((next: Partial<GammaMoveConfig>) => {
    setDraft((prev) => ({ ...(prev ?? server!), ...next }));
  }, [server]);
  const apply = React.useCallback(() => {
    if (!draft) return;
    setCfg.mutate(draft, { onSuccess: () => {
      setDraft(null);
      notifyOrder({ kind: 'info', title: 'Settings saved', message: 'Gamma Unwind S1.6.0 settings applied.' });
    }});
  }, [draft, setCfg]);

  if (isLoading || !cfg || !server || !defaults || !strategy || !effective) {
    return <PanelCard><div style={{ color: DIM, fontSize: 12 }}>Loading Gamma Unwind settings…</div></PanelCard>;
  }

  return <>
    <EnginePowerHeader name={strategy.name} tagline={strategy.tagline}
      on={cfg.enabled} liveOn={server.enabled} busy={setCfg.isPending}
      onToggle={() => patch({ enabled: !cfg.enabled })}
      runningNote="Daily zones → HOI-1 → hidden E2 → live E1 → four-gate tier."
      offNote="Off. Watchlists, E1 alerts and new Gamma-Unwind entries are disabled." />

    <ConfigNote><span><strong>{strategy.reference_package} · {strategy.spec_version} · {strategy.edition} edition.</strong>{' '}{strategy.how_it_works}</span></ConfigNote>
    <ConfigNote><span><strong>Execution is an adapter, not part of the supplied client.</strong>{' '}
      Only ENTRY can execute. STRONG WATCHLIST alerts only; WATCHLIST stays on the board.
      Live Gamma Unwind trades intraday contracts only (expiry today or tomorrow, exit 15:20 IST).
      A swing has no broker premium stop before T1, so a live swing ENTRY is refused and shown as NOT LIVE.
    </span></ConfigNote>
    {(data.warnings ?? []).map((w) => <ConfigNote key={w}><span>{w}</span></ConfigNote>)}

    <Section title="Execution adapter" description="Sterling-only controls around the unchanged signal tier."
      summary={String(cfg.execution_lots) + ' lot(s) · ' + (cfg.auto_execute ? 'ALGO' : 'manual') + ' · ' + cfg.stop_mode}
      persistKey="gamma-unwind-execution" defaultOpen>
      <Field label="Algo execute" hint="Requires both this switch and shared Kite ALGO. Only ENTRY may increase exposure.">
        <Switch checked={cfg.auto_execute} label={cfg.auto_execute ? 'On' : 'Off'}
          onChange={() => patch({ auto_execute: !cfg.auto_execute })} />
      </Field>
      <NumberField label="Execution lots" hint="Sterling execution size; not a reference signal input."
        value={cfg.execution_lots} defaultValue={defaults.execution_lots}
        onChange={(v) => patch({ execution_lots: v })} min={1} max={100} step={1} />
      <Field label="Premium stop transport" hint="Only where the reference defines a premium stop: DTE≤1 base-low and post-T1 entry stop.">
        <ChoiceRow value={cfg.stop_mode} options={STOP_MODE_OPTIONS} onChange={(v) => patch({ stop_mode: v })} />
      </Field>
      <ConfigNote>
        Live entry requires a broker-held stop: choose Broker + monitor (recommended) or Broker only.
      </ConfigNote>
    </Section>

    <Section title="Instruments"
      description="Reference universe: every currently listed F&O single-stock underlying; indices and NSETEST contracts are excluded."
      summary={cfg.scan_all_stocks ? 'all ' + eligible.length + ' listed F&O stocks' : cfg.scan_stocks.length + ' selected stocks'}
      persistKey="gamma-unwind-instruments" defaultOpen>
      <InstrumentsGroup idPrefix="gamma-unwind" indices={[]} stocks={cfg.scan_stocks}
        allStocks={cfg.scan_all_stocks} stockContracts={cfg.stock_contracts} allowEmptyIndices
        onChange={(next) => patch({
          ...(next.scan_stocks !== undefined ? { scan_stocks: next.scan_stocks } : {}),
          ...(next.scan_all_stocks !== undefined ? { scan_all_stocks: next.scan_all_stocks } : {}),
          ...(next.scan_stock_contracts !== undefined ? { stock_contracts: next.scan_stock_contracts } : {}),
        })} />
    </Section>

    <Section title="Daily zones" description="Pine-style supply/demand zones."
      summary={'pivot ' + cfg.zone_pivot_len + ' · ATR ' + cfg.zone_atr_len + ' · depth ' + cfg.zone_depth}
      persistKey="gamma-unwind-zones" defaultOpen>
      <NumberField label="Pivot length" hint="Left/right daily bars used to confirm a pivot."
        value={cfg.zone_pivot_len} defaultValue={defaults.zone_pivot_len}
        onChange={(v) => patch({ zone_pivot_len: v })} min={1} max={50} step={1} />
      <NumberField label="ATR length" hint="Wilder/Pine RMA ATR used for zone width."
        value={cfg.zone_atr_len} defaultValue={defaults.zone_atr_len}
        onChange={(v) => patch({ zone_atr_len: v })} min={2} max={200} step={1} />
      <NumberField label="Zone depth" hint="Actual buffer is ATR × depth / 10."
        value={cfg.zone_depth} defaultValue={defaults.zone_depth}
        onChange={(v) => patch({ zone_depth: v })} min={0} max={20} step={0.1} />
      <NumberField label="Guard multiplier" hint="New midpoint must clear this many ATRs from existing zone midpoints."
        value={cfg.zone_guard_mult} defaultValue={defaults.zone_guard_mult}
        onChange={(v) => patch({ zone_guard_mult: v })} min={0} max={10} step={0.1} />
      <NumberField label="Zone memory" hint="Maximum live buy and sell zones retained."
        value={cfg.zone_memory} defaultValue={defaults.zone_memory}
        onChange={(v) => patch({ zone_memory: v })} min={1} max={100} step={1} />
      <Field label="Break zones on wick" hint="Off in the reference: closes retire zones; on uses high/low.">
        <Switch checked={cfg.zone_break_on_wick} label={cfg.zone_break_on_wick ? 'Wick' : 'Close'}
          onChange={() => patch({ zone_break_on_wick: !cfg.zone_break_on_wick })} />
      </Field>
      <NumberField label="Daily bars" hint="Maximum daily history used to reconstruct live zones."
        value={cfg.zone_daily_bars} defaultValue={defaults.zone_daily_bars}
        onChange={(v) => patch({ zone_daily_bars: v })} min={100} max={2000} step={50} />
    </Section>

    <Section title="Watchlist and strike" description="Zone proximity, expiry lists and client HOI selection."
      summary={'stock ±' + cfg.stock_band_pct + '% · strike ±' + cfg.strike_band_pct + '% · HOI-' + effective.top_n_oi}
      persistKey="gamma-unwind-watchlist" defaultOpen>
      <NumberField label="Stock band" hint="Zone-edge proximity required before the stock enters a list."
        value={cfg.stock_band_pct} defaultValue={defaults.stock_band_pct}
        onChange={(v) => patch({ stock_band_pct: v })} min={0} max={20} step={0.1} suffix="%" />
      <NumberField label="Strike band" hint="Maximum strike distance from the relevant zone edge."
        value={cfg.strike_band_pct} defaultValue={defaults.strike_band_pct}
        onChange={(v) => patch({ strike_band_pct: v })} min={0} max={20} step={0.1} suffix="%" />
      <NumberField label="strategy.yaml top_n_oi" hint="Reference YAML is 2; client edition is hard-pinned to effective HOI-1."
        value={cfg.top_n_oi} defaultValue={defaults.top_n_oi} onChange={() => {}} min={1} max={10} step={1} disabled />
      <NumberField label="Minimum premium" hint="Minimum option premium in ticks; 0 disables."
        value={cfg.min_premium_ticks} defaultValue={defaults.min_premium_ticks}
        onChange={(v) => patch({ min_premium_ticks: v })} min={0} max={1000} step={1} suffix=" ticks" />
      <NumberField label="Minimum OI" hint="Option OI floor in contract lots."
        value={cfg.min_oi_lots} defaultValue={defaults.min_oi_lots}
        onChange={(v) => patch({ min_oi_lots: v })} min={0} max={10000} step={1} suffix=" lots" />
      <NumberField label="Previous-session turnover" hint="Reference floor; fails open when prior-session values are unavailable."
        value={cfg.min_prev_turnover_rs} defaultValue={defaults.min_prev_turnover_rs}
        onChange={(v) => patch({ min_prev_turnover_rs: v })} min={0} step={10000} suffix="₹" />
      <NumberField label="Expiry switch threshold" hint="At/below this NSE trading-DTE, build four lists; otherwise two."
        value={cfg.expiry_dte_threshold} defaultValue={defaults.expiry_dte_threshold}
        onChange={(v) => patch({ expiry_dte_threshold: v })} min={0} max={30} step={1} suffix=" trading days" />
    </Section>

    <Section title="Watcher" description="Hidden E2 closed-bar conditions and live E1 breakout."
      summary={'OI ' + (cfg.oi_drop_alert * 100).toFixed(0) + '% · SMA' + cfg.vol_sma_len + ' · base ' + cfg.consol_bars}
      persistKey="gamma-unwind-watcher" defaultOpen>
      <ConfigNote><span>Client-fixed timeframes: {effective.base_tf_min}m base · {effective.signal_tf_min}m signal · {effective.display_tf_min}m display. E2 remains internal; only live E1 is surfaced.</span></ConfigNote>
      <NumberField label="OI drop alert" hint="A threshold from today's option OI peak."
        value={cfg.oi_drop_alert * 100} defaultValue={defaults.oi_drop_alert * 100}
        onChange={(v) => patch({ oi_drop_alert: v / 100 })} min={0.1} max={100} step={0.5} suffix="%" />
      <NumberField label="OI tolerance" hint="8% with 1% tolerance means A passes at 7%."
        value={cfg.oi_drop_tolerance * 100} defaultValue={defaults.oi_drop_tolerance * 100}
        onChange={(v) => patch({ oi_drop_tolerance: v / 100 })} min={0} max={50} step={0.5} suffix="%" />
      <NumberField label="Strong OI drop" hint="Marks a strong unwind."
        value={cfg.oi_drop_strong * 100} defaultValue={defaults.oi_drop_strong * 100}
        onChange={(v) => patch({ oi_drop_strong: v / 100 })} min={0.1} max={100} step={0.5} suffix="%" />
      <NumberField label="Re-arm extra drop" hint="Further OI fall required after a completed cycle."
        value={cfg.oi_rearm_extra * 100} defaultValue={defaults.oi_rearm_extra * 100}
        onChange={(v) => patch({ oi_rearm_extra: v / 100 })} min={0} max={100} step={0.5} suffix="%" />
      <NumberField label="Volume SMA" hint="Closed 5-minute bars used for normal volume."
        value={cfg.vol_sma_len} defaultValue={defaults.vol_sma_len}
        onChange={(v) => patch({ vol_sma_len: v })} min={2} max={200} step={1} />
      <NumberField label="Volume bars of last 3" hint="B requires this many of the last three closed bars above SMA."
        value={cfg.vol_build_min_of_last3} defaultValue={defaults.vol_build_min_of_last3}
        onChange={(v) => patch({ vol_build_min_of_last3: v })} min={1} max={3} step={1} />
      <NumberField label="Consolidation bars" hint="Closed 5-minute bars in the E2 base."
        value={cfg.consol_bars} defaultValue={defaults.consol_bars}
        onChange={(v) => patch({ consol_bars: v })} min={2} max={50} step={1} />
      <NumberField label="Consolidation ATR" hint="ATR length for C."
        value={cfg.consol_atr_len} defaultValue={defaults.consol_atr_len}
        onChange={(v) => patch({ consol_atr_len: v })} min={2} max={100} step={1} />
      <NumberField label="Range / ATR" hint="C primary range limit."
        value={cfg.consol_range_atr} defaultValue={defaults.consol_range_atr}
        onChange={(v) => patch({ consol_range_atr: v })} min={0} max={10} step={0.1} suffix="×" />
      <NumberField label="Drift range / ATR" hint="Alternative C limit when base drift is non-positive."
        value={cfg.consol_drift_range_atr} defaultValue={defaults.consol_drift_range_atr}
        onChange={(v) => patch({ consol_drift_range_atr: v })} min={0} max={10} step={0.1} suffix="×" />
      <NumberField label="Minimum base range" hint="H minimum base range in option ticks."
        value={cfg.h_min_range_ticks} defaultValue={defaults.h_min_range_ticks}
        onChange={(v) => patch({ h_min_range_ticks: v })} min={0} max={100} step={0.5} suffix=" ticks" />
      <NumberField label="Maximum zero-volume slots" hint="Missing 5-minute clock slots count as zero-volume."
        value={cfg.h_max_zero_vol_slots} defaultValue={defaults.h_max_zero_vol_slots}
        onChange={(v) => patch({ h_max_zero_vol_slots: v })} min={0} max={cfg.consol_bars} step={1} />
    </Section>

    <Section title="Four gates" description="4 passes ENTRY, 3 STRONG WATCHLIST, 0–2 WATCHLIST."
      summary={cfg.v1_direction_mode + ' direction · zone ' + cfg.zone_max_gap_pct + '%'}
      persistKey="gamma-unwind-gates" defaultOpen>
      <Field label="Gates enabled" hint="Disabled gates do not count as passes.">
        <Switch checked={cfg.gates_enabled} label={cfg.gates_enabled ? 'On' : 'Off'}
          onChange={() => patch({ gates_enabled: !cfg.gates_enabled })} />
      </Field>
      <Field label="All lists" hint="Client S1.6.0 applies the four gates to every list; fixed in this edition.">
        <Switch checked={cfg.gates_all_lists} label="All lists" onChange={() => {}} disabled />
      </Field>
      <NumberField label="Base zone gap" hint="Minimum zone-gate allowance."
        value={cfg.zone_max_gap_pct} defaultValue={defaults.zone_max_gap_pct}
        onChange={(v) => patch({ zone_max_gap_pct: v })} min={0} max={20} step={0.1} suffix="%" />
      <NumberField label="ATR zone factor" hint="Dynamic allowance factor × daily ATR%."
        value={cfg.v1_zone_atr_k} defaultValue={defaults.v1_zone_atr_k}
        onChange={(v) => patch({ v1_zone_atr_k: v })} min={0} max={10} step={0.1} suffix="× ATR%" />
      <NumberField label="Dynamic zone cap" hint="Maximum ATR-expanded gap."
        value={cfg.v1_zone_max_pct} defaultValue={defaults.v1_zone_max_pct}
        onChange={(v) => patch({ v1_zone_max_pct: v })} min={cfg.zone_max_gap_pct} max={20} step={0.1} suffix="%" />
      <NumberField label="Liquidity bars" hint="Closed 5-minute bars for volume-lots and zero-volume gate."
        value={cfg.liq_sma_bars} defaultValue={defaults.liq_sma_bars}
        onChange={(v) => patch({ liq_sma_bars: v })} min={2} max={200} step={1} />
      <NumberField label="Minimum average volume" hint="Liquidity-gate minimum average traded lots."
        value={cfg.liq_min_avg_lots} defaultValue={defaults.liq_min_avg_lots}
        onChange={(v) => patch({ liq_min_avg_lots: v })} min={0} max={10000} step={0.5} suffix=" lots" />
      <NumberField label="Maximum zero-volume bars" hint="Liquidity-gate maximum zero-volume closed bars."
        value={cfg.liq_max_zero_vol_bars} defaultValue={defaults.liq_max_zero_vol_bars}
        onChange={(v) => patch({ liq_max_zero_vol_bars: v })} min={0} max={cfg.liq_sma_bars} step={1} />
      <Field label="Direction gate" hint="Underlying same-day 5-minute VWAP and 30-minute drift.">
        <Switch checked={cfg.direction_enabled} label={cfg.direction_enabled ? 'On' : 'Off'}
          onChange={() => patch({ direction_enabled: !cfg.direction_enabled })} />
      </Field>
      <Field label="Direction mode">
        <ChoiceRow value={cfg.v1_direction_mode} options={DIRECTION_OPTIONS}
          onChange={(v) => patch({ v1_direction_mode: v })} />
      </Field>
      <Field label="Unwind gate" hint="E2 close must improve on OI-peak-bar close by configured ticks.">
        <Switch checked={cfg.unwind_enabled} label={cfg.unwind_enabled ? 'On' : 'Off'}
          onChange={() => patch({ unwind_enabled: !cfg.unwind_enabled })} />
      </Field>
      <NumberField label="Unwind ticks" hint="Minimum improvement above OI-peak-bar close."
        value={cfg.unwind_min_ticks} defaultValue={defaults.unwind_min_ticks}
        onChange={(v) => patch({ unwind_min_ticks: v })} min={0} max={100} step={0.5} suffix=" ticks" />
    </Section>

    <Section title="Trade follow-up" description="Reference T1/T2, D68 and hold horizon."
      summary={'T1 +20% · T2 +40% · ' + cfg.swing_days + ' trading days'}
      persistKey="gamma-unwind-hold" defaultOpen>
      <ConfigNote><span>DTE≤1 is intraday: base-low stop and 15:20 exit. Swing has no premium stop before T1; D68 uses the underlying daily close. T1 moves the premium stop to entry; T2 closes. “Book part” at T1 has no fraction in the reference, so Sterling does not invent one.</span></ConfigNote>
      <NumberField label="Swing trading days" hint="Maximum swing hold before the reference 15:20 exit."
        value={cfg.swing_days} defaultValue={defaults.swing_days}
        onChange={(v) => patch({ swing_days: v })} min={1} max={20} step={1} suffix=" trading days" />
    </Section>

    <Section title="Schedule" description="Reference IST refresh times."
      summary={cfg.build_watchlists_times.join(' / ') + ' watchlists'}
      persistKey="gamma-unwind-schedule">
      <TimeField label="Mark levels #1" value={timeAt(cfg.mark_levels_times, 0, '09:20')}
        hint="Invalidate/rebuild daily level state." onChange={(v) => patch({ mark_levels_times: setTime(cfg.mark_levels_times, 0, v) })} />
      <TimeField label="Mark levels #2" value={timeAt(cfg.mark_levels_times, 1, '16:00')}
        hint="After-market level refresh." onChange={(v) => patch({ mark_levels_times: setTime(cfg.mark_levels_times, 1, v) })} />
      <TimeField label="Build watchlists #1" value={timeAt(cfg.build_watchlists_times, 0, '09:20')}
        hint="Morning watchlist build." onChange={(v) => patch({ build_watchlists_times: setTime(cfg.build_watchlists_times, 0, v) })} />
      <TimeField label="Build watchlists #2" value={timeAt(cfg.build_watchlists_times, 1, '13:15')}
        hint="Afternoon watchlist refresh." onChange={(v) => patch({ build_watchlists_times: setTime(cfg.build_watchlists_times, 1, v) })} />
      <NumberField label="Token poll" hint="Reference standalone-client value. Sterling account/session keeper owns token refresh, so this is informational here."
        value={cfg.token_poll_minutes} defaultValue={defaults.token_poll_minutes}
        onChange={() => {}} min={1} max={120} step={1} suffix=" min" disabled />
    </Section>

    <AdvancedSection count={7}>
      <NumberField label="Telegram group rate" hint="Reference alert group-rate setting."
        value={cfg.alert_group_rate_per_min} defaultValue={defaults.alert_group_rate_per_min}
        onChange={(v) => patch({ alert_group_rate_per_min: v })} min={1} max={1000} step={1} suffix="/min" />
      <NumberField label="Telegram per-chat rate" hint="Reference per-chat rate."
        value={cfg.alert_per_chat_rate_per_sec} defaultValue={defaults.alert_per_chat_rate_per_sec}
        onChange={(v) => patch({ alert_per_chat_rate_per_sec: v })} min={0.1} max={20} step={0.1} suffix="/sec" />
      <TimeField label="Late-day warning" value={cfg.late_day_warn_ist}
        hint="Signals at/after this IST time carry the day-closing warning."
        onChange={(v) => patch({ late_day_warn_ist: v })} />
      <NumberField label="Expiry warning" hint="Warn inside this trading-DTE."
        value={cfg.expiry_warn_dte} defaultValue={defaults.expiry_warn_dte}
        onChange={(v) => patch({ expiry_warn_dte: v })} min={0} max={30} step={1} suffix=" trading days" />
      <NumberField label="Base timeframe" hint="Client-fixed." value={cfg.base_tf_min}
        defaultValue={defaults.base_tf_min} onChange={() => {}} min={1} step={1} suffix="m" disabled />
      <NumberField label="Signal timeframe" hint="Client-fixed." value={cfg.signal_tf_min}
        defaultValue={defaults.signal_tf_min} onChange={() => {}} min={1} step={1} suffix="m" disabled />
      <NumberField label="Display timeframe" hint="Client-fixed dashboard timeframe."
        value={cfg.display_tf_min} defaultValue={defaults.display_tf_min} onChange={() => {}} min={1} step={1} suffix="m" disabled />
    </AdvancedSection>

    <SettingsDraftBar dirty={dirty} saving={setCfg.isPending} onApply={apply}
      onDiscard={() => setDraft(null)}
      onReset={() => {
        if (!resetConfirm) { setResetConfirm(true); return; }
        setDraft({ ...defaults }); setResetConfirm(false);
      }}
      resetConfirm={resetConfirm} />
  </>;
}

export default GammaMoveSettings;

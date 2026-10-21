import { openSettingsSection } from './config/registry';
import { DataSourceSelector } from '../truedata/TrueDataCredentialsPanel';
import React from 'react';
import { useKiteSettings } from '../../store/useKiteSettings';
import { useStagedConfigPatch } from './config/engineConfigDraft';
import { BORDER, DIM, MUTED, SOFT, Switch, TEXT } from './kiteSettingsPrimitives';
import { PanelCard, PanelSectionHeading } from './config/ConfigPrimitives';
import { TradingModeControls } from './TradingModeControls';
import { useIsSimulation } from '../../store/useViewStore';
import { SimDeniedNote } from '../sim/SimDeniedNote';
import { useReleaseEngineToggles } from '../../hooks/useEngineToggles';
import { useAlgoToggles } from '../../hooks/useAlgoToggles';
import { usePriceRulesSwitch } from '../../hooks/usePriceRulesSwitch';
import { notifyOrder } from '../../store/useKiteNotifications';
import type { EngineConfigModel, DeepItmMoneyness } from '../../types/kiteEngine';

type ProfileId = 'atm' | 'otm' | 'slight_itm' | 'deep_itm' | 'futures';

function deriveActiveProfile(cfg?: EngineConfigModel | null): ProfileId {
  if (!cfg) return 'atm';
  if (cfg.vehicle === 'futures' && cfg.directional_mode) return 'futures';
  const d = cfg.target_delta;
  if (cfg.vehicle === 'deep_itm_options' && cfg.directional_mode) {
    return (d ?? 0.85) >= 0.78 ? 'deep_itm' : 'slight_itm';
  }
  if (d != null && d < 0.45) return 'otm';
  return 'atm';
}

const PROFILES: Array<{
  id: ProfileId;
  label: string;
  delta: string;
  badge: string;
  desc: string;
}> = [
  { id: 'atm', label: 'ATM', delta: 'δ ≈ 0.50', badge: 'Most liquid', desc: 'Tightest spreads, standard option risk.' },
  { id: 'otm', label: 'OTM', delta: 'δ ≈ 0.28', badge: 'High leverage', desc: 'Lowest premium per lot, faster theta decay.' },
  { id: 'slight_itm', label: 'Slight ITM', delta: 'δ ≈ 0.65', badge: 'Lower theta', desc: 'Slower decay, more delta on breakouts.' },
  { id: 'deep_itm', label: 'Deep ITM', delta: 'δ ≈ 0.85', badge: 'Near futures', desc: 'Moves almost point-for-point with the underlying.' },
  { id: 'futures', label: 'Futures', delta: 'δ = 1.00', badge: 'Index futures', desc: 'No theta decay. Needs F&O margin.' },
];

const ITM_DEPTH_CHOICES: Array<{ id: DeepItmMoneyness; label: string; delta: string }> = [
  { id: 'ITM5', label: 'ITM-5', delta: 'δ ≈ 0.75' },
  { id: 'ITM10', label: 'ITM-10', delta: 'δ ≈ 0.85 (Standard)' },
  { id: 'ITM15', label: 'ITM-15', delta: 'δ ≈ 0.92' },
  { id: 'ITM20', label: 'ITM-20', delta: 'δ ≈ 0.96 (Near Futures)' },
];

const SPREAD_OPTIONS: Array<{ val: number | null; label: string; hint: string }> = [
  { val: 1.5, label: '1.5%', hint: 'Strict: liquid index options' },
  { val: 2.0, label: '2.0%', hint: 'Standard: guards illiquid stock strikes' },
  { val: 3.0, label: '3.0%', hint: 'Relaxed: moderately wider spreads' },
  { val: 5.0, label: '5.0%', hint: 'Wide: blocks only severe spreads' },
  { val: null, label: 'Off', hint: 'No spread check' },
];

const OI_FLOORS: Array<{ val: number | null; label: string }> = [
  { val: 500, label: '500 OI' },
  { val: 1000, label: '1,000 OI' },
  { val: 5000, label: '5,000 OI' },
  { val: null, label: 'Off' },
];

/**
 * Trading config owns the live status and safeguards, the shared master ALGO gate,
 * engine power switches, and re-scan membership. Strategy-specific automatic
 * arming lives on Algo Trade; the master ALGO switch is necessary for every
 * strategy but does not select which strategy is allowed to originate.
 */

export function RunningRow({ label, description, on, children }: {
  label: string;
  description: string;
  on?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      padding: '13px 0', borderTop: `1px solid ${BORDER}`,
    }}>
      <span aria-hidden title={on == null ? 'Armed individually per plan' : undefined} style={{
        width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
        background: on == null ? 'var(--k-blue)' : on ? 'var(--k-green)' : '#c7c7c7',
      }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ color: TEXT, fontSize: 12.5, fontWeight: 700 }}>{label}</div>
        <div style={{ color: MUTED, fontSize: 11, lineHeight: 1.5, marginTop: 2 }}>{description}</div>
      </div>
      {children}
    </div>
  );
}

const RESCANNABLE: Array<{ engine: string; label: string; note: string }> = [
  { engine: 'supertrend', label: 'SuperTrend', note: 'Triple SuperTrend across the configured universe' },
  { engine: 'gamma_move', label: 'Gamma Unwind', note: 'Daily zones → HOI-1 → hidden 5m E2 → live E1 → four gates' },
  { engine: 'ema_trend', label: 'EMA Trend', note: 'Cash-equity paper trends on four independent profiles' },
  { engine: 'snapback', label: 'Snapback', note: 'Daily swing and intraday scans on the active config' },
];

export function TradingModePanel() {
  const sim = useIsSimulation();
  // Staged: every choice here waits for Apply in the bottom bar.
  const staged = useStagedConfigPatch();
  const cfg = staged.cfg;
  const patchCfg = { mutate: (values: Partial<EngineConfigModel>) => staged.patch(values), isPending: staged.saving };
  const toggles = useReleaseEngineToggles();
  const priceRules = usePriceRulesSwitch(sim);
  const rescanStrategies = useKiteSettings((s) => s.rescanStrategies);
  const toggleRescanStrategy = useKiteSettings((s) => s.toggleRescanStrategy);

  const activeProfile = deriveActiveProfile(cfg);

  const onSelectProfile = (id: ProfileId) => {
    if (!cfg) return;
    let patch: Partial<EngineConfigModel> = {};
    if (id === 'atm') {
      patch = { vehicle: 'otm_options', directional_mode: false, target_delta: 0.50 };
    } else if (id === 'otm') {
      patch = { vehicle: 'otm_options', directional_mode: false, target_delta: 0.28 };
    } else if (id === 'slight_itm') {
      patch = { vehicle: 'deep_itm_options', directional_mode: true, target_delta: 0.65 };
    } else if (id === 'deep_itm') {
      patch = { vehicle: 'deep_itm_options', directional_mode: true, target_delta: 0.85, itm_depth: cfg.itm_depth || 'ITM10' };
    } else if (id === 'futures') {
      patch = { vehicle: 'futures', directional_mode: true, target_delta: null };
    }
    const targetVeh = patch.vehicle!;
    const currentVehs = cfg.enabled_vehicles || ['otm_options'];
    if (!currentVehs.includes(targetVeh)) {
      patch.enabled_vehicles = [...currentVehs, targetVeh];
    }
    patchCfg.mutate(patch);
  };

  return (
    <>
      <TradingModeControls />
      {sim ? <SimDeniedNote what="Choosing the market-data source" /> : <DataSourceSelector />}

      {/* Options & Strike Execution Config */}
      <PanelCard>
        <PanelSectionHeading
          title="Options & Strike Execution Config"
          description="Contract profile, liquidity filters and sizing."
        />
        <div style={{ padding: '0 18px 18px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Strike profile selector */}
          <div>
            <div style={{ fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 8, textTransform: 'uppercase' }}>
              Strike Moneyness & Delta Profile
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
              {PROFILES.map((p) => {
                const isSelected = activeProfile === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => onSelectProfile(p.id)}
                    disabled={patchCfg.isPending}
                    style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                      padding: '10px 12px', borderRadius: 8, textAlign: 'left', cursor: 'pointer',
                      background: isSelected ? 'rgba(240, 100, 40, 0.08)' : 'var(--k-surface-2)',
                      border: isSelected ? '1.5px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', marginBottom: 3 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 750, color: isSelected ? 'var(--k-brand)' : TEXT }}>
                        {p.label}
                      </span>
                      <span style={{ fontSize: 10, fontWeight: 650, color: isSelected ? 'var(--k-brand)' : MUTED }}>
                        {p.delta}
                      </span>
                    </div>
                    <div style={{ fontSize: 9.5, fontWeight: 650, color: isSelected ? 'var(--k-brand)' : DIM, marginBottom: 4 }}>
                      {p.badge}
                    </div>
                    <div style={{ fontSize: 10, color: MUTED, lineHeight: 1.35 }}>
                      {p.desc}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Deep ITM Depth (if active) */}
          {(activeProfile === 'deep_itm' || activeProfile === 'slight_itm') && (
            <div style={{ padding: '10px 12px', background: 'var(--k-surface-sunken-2)', borderRadius: 7, border: `1px solid ${BORDER}` }}>
              <div style={{ fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 6, textTransform: 'uppercase' }}>
                ITM Strike Depth
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {ITM_DEPTH_CHOICES.map((choice) => {
                  const isSel = (cfg?.itm_depth || 'ITM10') === choice.id;
                  return (
                    <button
                      key={choice.id}
                      type="button"
                      onClick={() => patchCfg.mutate({ itm_depth: choice.id })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-bg)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {choice.label} ({choice.delta})
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Execution Lot Sizing */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', paddingTop: 8, borderTop: `1px solid ${BORDER}` }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Maximum lots per automatic entry</div>
              <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>Hard ceiling on automatic entries. Does not size manual orders.</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {[1, 2, 5, 10, 25].map((l) => {
                const isSel = (cfg?.max_lots ?? 1) === l;
                return (
                  <button
                    key={l}
                    type="button"
                    onClick={() => patchCfg.mutate({ max_lots: l })}
                    disabled={patchCfg.isPending}
                    style={{
                      padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                      background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                      color: isSel ? 'var(--k-on-accent)' : TEXT,
                      border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                    }}
                  >
                    {l}L
                  </button>
                );
              })}
            </div>
          </div>

          {/* Real-world Slippage & Bid-Ask Spread Protection */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 10, borderTop: `1px solid ${BORDER}` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Max bid-ask spread</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Skips the order if the spread exceeds this % of mid price.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {SPREAD_OPTIONS.map((opt) => {
                  const isSel = (cfg ? cfg.max_spread_pct : 2.0) === opt.val;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      title={opt.hint}
                      onClick={() => patchCfg.mutate({ max_spread_pct: opt.val })}
                      disabled={patchCfg.isPending}
                      aria-pressed={isSel}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Minimum open interest</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Skips strikes with thin open interest.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5 }}>
                {OI_FLOORS.map((opt) => {
                  const isSel = (cfg?.min_oi ?? null) === opt.val;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => patchCfg.mutate({ min_oi: opt.val })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, color: TEXT }}>Stop placement</div>
                <div style={{ fontSize: 10.5, color: MUTED, marginTop: 2 }}>
                  Broker trigger order, engine tick monitor, or both.
                </div>
              </div>
              <div style={{ display: 'flex', gap: 5 }}>
                {[
                  { id: 'both', label: 'Broker + Monitor' },
                  { id: 'broker', label: 'Broker SL-M' },
                  { id: 'monitor', label: 'Software Trail' },
                ].map((m) => {
                  const isSel = (cfg?.stop_mode || 'both') === m.id;
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => patchCfg.mutate({ stop_mode: m.id as any })}
                      disabled={patchCfg.isPending}
                      style={{
                        padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650, cursor: 'pointer',
                        background: isSel ? 'var(--k-brand)' : 'var(--k-surface-2)',
                        color: isSel ? 'var(--k-on-accent)' : TEXT,
                        border: isSel ? '1px solid var(--k-brand)' : `1px solid ${BORDER}`,
                      }}
                    >
                      {m.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </PanelCard>

      <PanelCard>
        <PanelSectionHeading
          title="What is running"
          description="Control each strategy's new entries. Price Rules plans are armed separately. Existing positions and safety services remain managed while off."
        />
        <div style={{ padding: '2px 18px 16px' }}>
          {toggles.map((engine) => (
            <RunningRow
              key={engine.id}
              label={engine.label}
              description={engine.description}
              on={engine.enabled}
            >
              <Switch
                checked={engine.enabled}
                label={engine.label}
                disabled={!engine.toggle || engine.pending}
                onChange={() => engine.toggle?.()}
              />
            </RunningRow>
          ))}
          <RunningRow label="Price Rules" on={priceRules.enabled}
            description={priceRules.enabled
              ? "On. Individually armed plans can trigger when safety checks pass."
              : "Off. New entries are blocked; owned positions and protection remain managed."}>
            <Switch checked={priceRules.enabled} label="Price Rules"
              disabled={!priceRules.toggle || priceRules.pending}
              onChange={() => priceRules.toggle?.()} />
            <button
              type="button"
              aria-label="Open Price Rules plans and owned positions"
              onClick={() => openSettingsSection('priceRules')}
              style={{ padding: '4px 9px', borderRadius: 5, fontSize: 11, fontWeight: 650,
                background: 'var(--k-surface-2)', color: TEXT, border: `1px solid ${BORDER}`, cursor: 'pointer' }}
            >Plans →</button>
            {priceRules.error && <span role="alert" style={{ color: 'var(--k-danger)', fontSize: 11 }}>Price Rules switch failed: {priceRules.error.message}</span>}
          </RunningRow>
        </div>

        <div style={{
          padding: '12px 18px', background: SOFT, borderTop: `1px solid ${BORDER}`,
          color: DIM, fontSize: 10.5, lineHeight: 1.5,
        }}>
          Master ALGO is per user. Each strategy also has its own arm switch and can still be blocked by risk or safety gates.
        </div>
      </PanelCard>

      <PanelCard>
        <PanelSectionHeading
          title="Included in re-scan"
          description="Strategies the re-scan button runs."
        />
        <div style={{ padding: '2px 18px 16px', display: 'grid', gap: 2 }}>
          {RESCANNABLE.map(({ engine, label, note }) => {
            const isEngineRunning = toggles.find((t) => t.id === engine)?.enabled ?? false;
            const checked = isEngineRunning && rescanStrategies[engine] !== false;
            return (
              <label
                key={engine}
                title={isEngineRunning ? note : `${label} is off. Turn it on above to re-scan it.`}
                style={{
                  minHeight: 30, display: 'flex', alignItems: 'center', gap: 8,
                  color: isEngineRunning ? 'var(--k-text)' : 'var(--k-dim)',
                  fontSize: 11, cursor: isEngineRunning ? 'pointer' : 'not-allowed',
                  opacity: isEngineRunning ? 1 : 0.5,
                }}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!isEngineRunning}
                  onChange={() => isEngineRunning && toggleRescanStrategy(engine)}
                  style={{ width: 14, height: 14, margin: 0, accentColor: 'var(--k-orange)', cursor: isEngineRunning ? 'pointer' : 'not-allowed' }}
                />
                <span style={{ fontWeight: 600 }}>{label}</span>
                <span style={{ color: 'var(--k-dim)', fontSize: 10 }}>
                  {isEngineRunning ? note : '(engine off)'}
                </span>
              </label>
            );
          })}
          <div style={{ marginTop: 8, fontSize: 10, lineHeight: 1.5, color: 'var(--k-dim)' }}>
            A strategy switched off above is skipped whatever is ticked here.
          </div>
        </div>
      </PanelCard>
    </>
  );
}

export default TradingModePanel;

import React from 'react';
import type { SnapbackConfig } from '../hooks/useSnapback';
import { ChoiceRow, Field, NumberField, Section, Switch } from './kite/kiteSettingsPrimitives';
import { ConfigNote } from './kite/config/ConfigPrimitives';

type NumericKey = {
  [K in keyof SnapbackConfig]-?: NonNullable<SnapbackConfig[K]> extends number ? K : never
}[keyof SnapbackConfig];
type Control = {
  key: NumericKey; label: string; fallback: number; hint: string;
  min: number; max: number; step?: number; suffix?: string;
};

const PLAN: Control[] = [
  { key: 'scalp_target_points', label: 'Requested target', fallback: 5, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'Profit per unit to aim for. May rise a little to cover costs.' },
  { key: 'scalp_stop_points', label: 'Initial stop distance', fallback: 4, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'Loss per unit before Sterling exits. A price gap can lose more.' },
  { key: 'scalp_trail_points', label: 'Runner trailing distance', fallback: 2, min: 0.05, max: 1000, step: 0.05, suffix: 'pts', hint: 'Once the trade runs, the stop follows the best price by this gap. It only moves up.' },
  { key: 'scalp_lock_points', label: 'Runner profit lock', fallback: 1, min: 0, max: 1000, step: 0.05, suffix: 'pts', hint: 'Extra profit to aim for when a strong candle lets the trade run.' },
  { key: 'scalp_max_hold_bars', label: 'Initial holding limit', fallback: 20, min: 1, max: 375, suffix: 'bars', hint: 'Exit after this long if the target is not hit.' },
  { key: 'scalp_runner_max_bars', label: 'Runner holding limit', fallback: 60, min: 1, max: 375, suffix: 'bars', hint: 'Longest a trade may stay open. Day-end exit still applies.' },
];

const RISK: Control[] = [
  { key: 'capital_inr', label: 'Capital', fallback: 100000, min: 1000, max: 1e10, step: 1000, suffix: '₹', hint: 'Money in the account (₹). Lots are sized to fit it.' },
  { key: 'scalp_risk_pct', label: 'Risk per trade', fallback: 0.5, min: 0.01, max: 5, step: 0.05, suffix: '%', hint: 'Most you plan to lose on one trade, with costs.' },
  { key: 'premium_pct_of_capital', label: 'Premium budget', fallback: 2, min: 0.01, max: 100, step: 0.5, suffix: '%', hint: 'Most premium to spend on one trade. A lot that costs more is skipped.' },
  { key: 'lots', label: 'Fixed lots requested', fallback: 1, min: 1, max: 100, hint: 'Lots per trade. Cash and risk limits can lower it.' },
  { key: 'max_lots', label: 'Maximum lots', fallback: 5, min: 1, max: 100, hint: 'More lots means bigger wins and bigger losses.' },
  { key: 'scalp_daily_loss_pct', label: 'Daily loss limit', fallback: 2, min: 0.01, max: 20, step: 0.1, suffix: '%', hint: 'Stop new trades for the day after losing this much.' },
  { key: 'scalp_max_trades_per_day', label: 'Daily trade limit', fallback: 6, min: 1, max: 100, hint: 'Most trades per day.' },
  { key: 'scalp_cooldown_bars', label: 'Entry cooldown', fallback: 5, min: 0, max: 375, suffix: 'bars', hint: 'Completed bars to wait before another entry in the same instrument.' },
  { key: 'scalp_round_trip_cost_points', label: 'Variable round-trip costs', fallback: 1, min: 0, max: 1000, step: 0.05, suffix: 'pts', hint: 'Spread, fees and slippage per unit, in and out.' },
  { key: 'scalp_fixed_cost_inr', label: 'Fixed round-trip costs', fallback: 40, min: 0, max: 100000, step: 1, suffix: '₹', hint: 'Fixed charges per trade (₹), on top of the above.' },
  { key: 'scalp_min_net_rr', label: 'Minimum net reward/risk', fallback: 1, min: 0.1, max: 10, step: 0.1, suffix: 'x', hint: 'Profit after costs must be at least this many times the loss.' },
];

const FILTERS: Control[] = [
  { key: 'scalp_max_adx', label: 'Maximum entry ADX', fallback: 25, min: 1, max: 100, step: 1, hint: 'Skip trades against a strong trend.' },
  { key: 'scalp_min_relative_volume', label: 'Minimum relative volume', fallback: 0, min: 0, max: 20, step: 0.1, suffix: 'x', hint: 'Minimum volume. 0 = off.' },
  { key: 'target_delta', label: 'Target delta', fallback: 0.7, min: 0.05, max: 0.95, step: 0.05, hint: 'How close to the money the option is (delta).' },
  { key: 'min_dte', label: 'Minimum days to expiry', fallback: 40, min: 1, max: 365, suffix: 'd', hint: 'Which expiry to trade.' },
  { key: 'max_dte', label: 'Maximum days to expiry', fallback: 60, min: 1, max: 365, suffix: 'd', hint: 'Upper expiry bound. Keep this at or above the minimum expiry.' },
  { key: 'min_option_premium', label: 'Minimum premium', fallback: 10, min: 0, max: 10000, suffix: '₹', hint: 'Exclude very low premiums where tick rounding can dominate the move.' },
  { key: 'max_spread_pct', label: 'Maximum quoted spread', fallback: 2, min: 0, max: 100, step: 0.1, suffix: '%', hint: 'Skip options with a wider spread than this.' },
  { key: 'min_option_oi', label: 'Minimum option open interest', fallback: 0, min: 0, max: 1e9, step: 100, hint: 'Skip options with less open interest than this.' },
];

const timeLabel = (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;

export function SnapbackScalpSettings({ cfg, defaults, patch }: {
  cfg: SnapbackConfig; defaults: SnapbackConfig; patch: (next: Partial<SnapbackConfig>) => void;
}) {
  const controls = (items: Control[]) => items.map(({ key, fallback, ...props }) => (
    <NumberField key={key} {...props} value={cfg[key] ?? defaults[key] ?? fallback}
      disabled={(key === 'premium_pct_of_capital' && cfg.sizing_mode !== 'PREMIUM_PCT')
        || (key === 'lots' && cfg.sizing_mode !== 'LOTS')}
      defaultValue={defaults[key]} onChange={(value) => patch({ [key]: value })} />
  ));
  const times = [
    ['scalp_entry_start_minute', 'Entry start', 575],
    ['scalp_entry_end_minute', 'Last entry', 885],
    ['scalp_square_off_minute', 'Square-off', 915],
  ] as const;
  return <>
    <ConfigNote><span>
      <strong>Mode-specific execution.</strong> Daily swing results do not validate other trading modes.
      All target, stop and trail points refer to <strong>option premium</strong>, not the underlying index.
      Small targets can lose money after costs; no entry is guaranteed profitable.
      Automatic entries require the entry switches and broker safety checks. Filled positions retain their stop, trailing and holding policies.
    </span></ConfigNote>
    <Section title="Target, runner and timing" description="Small target first. A strong move can run with a tighter stop."
      summary="Premium points and completed bars" defaultOpen persistKey="snapback-scalp-plan">
      <Field label="Candle interval" hint="Candle size. Scalp 1 min, intraday 15 min, overnight 60 min.">
        <ChoiceRow value={String(cfg.scalp_timeframe_minutes ?? defaults.scalp_timeframe_minutes ?? 1)}
          options={(cfg.trading_mode === 'overnight' ? ['30', '60'] : cfg.trading_mode === 'ultra_scalping' ? ['1'] : cfg.trading_mode === 'intraday' ? ['5', '15', '30'] : ['1', '3', '5']).map((value) => ({ value, label: `${value} min` }))}
          onChange={(value) => patch({ scalp_timeframe_minutes: Number(value) })} />
      </Field>
      {controls(PLAN)}
      {times.map(([key, label, fallback]) => <Field key={key} label={`${label} (IST)`} hint="Start, then last entry, then day-end exit. Exchange time.">
        <input type="time" aria-label={`${label} (IST)`} min="09:15" max="15:25" value={timeLabel(cfg[key] ?? defaults[key] ?? fallback)}
          onChange={(e) => {
            if (!e.target.value) return;
            const [hour, minute] = e.target.value.split(':').map(Number);
            patch({ [key]: hour * 60 + minute });
          }} />
      </Field>)}
    </Section>
    <Section title="Risk and trading costs" description="Skip unaffordable lots and price the target after costs."
      summary="Per-trade budget and daily limits" defaultOpen persistKey="snapback-scalp-risk">
      <Field label="Sizing" hint="Cash, max lots and risk limits still apply.">
        <ChoiceRow value={cfg.sizing_mode} options={[
          { value: 'PREMIUM_PCT', label: 'Premium budget' }, { value: 'LOTS', label: 'Fixed lots' },
        ]} onChange={(sizing_mode) => patch({ sizing_mode })} />
      </Field>
      {controls(RISK)}
    </Section>
    <Section title="Entry and contract filters" description="Filters pick setups. Liquidity decides if the option is tradable."
      summary="ADX, volume, delta and liquidity" persistKey="snapback-scalp-filters">
      {controls(FILTERS)}
      <Field label="Fade weakness too" hint="Also buy calls after a sharp fall, not only puts after a sharp rise.">
        <Switch checked={cfg.allow_fade_down} label={cfg.allow_fade_down ? 'Both sides' : 'Puts into strength only'}
          onChange={() => patch({ allow_fade_down: !cfg.allow_fade_down })} />
      </Field>
    </Section>
    <Section title="Ablation indicator experiments" description="Extra filters to test one at a time. All off by default."
      summary="EMA9, ADX, PCR, OI, VWAP toggles" persistKey="snapback-scalp-experiments">
      <Field label="EMA9 reversal confirmation" hint="Require candle close to confirm direction across EMA9 before entry.">
        <Switch checked={!!cfg.use_ema_confirmation} label={cfg.use_ema_confirmation ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_ema_confirmation: !cfg.use_ema_confirmation })} />
      </Field>
      <Field label="ADX14 trend filter" hint="Skip trades when ADX is above this (strong trend).">
        <Switch checked={!!cfg.use_adx_filter} label={cfg.use_adx_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_adx_filter: !cfg.use_adx_filter })} />
      </Field>
      <Field label="PCR option chain filter" hint="Optional synchronized put/call ratio threshold check.">
        <Switch checked={!!cfg.use_pcr_filter} label={cfg.use_pcr_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_pcr_filter: !cfg.use_pcr_filter })} />
      </Field>
      <Field label="OI change filter" hint="Optional contract open interest change filter.">
        <Switch checked={!!cfg.use_oi_filter} label={cfg.use_oi_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_oi_filter: !cfg.use_oi_filter })} />
      </Field>
      <Field label="VWAP context filter" hint="Optional Volume Weighted Average Price context check.">
        <Switch checked={!!cfg.use_vwap_filter} label={cfg.use_vwap_filter ? 'Enabled' : 'Disabled (Baseline)'}
          onChange={() => patch({ use_vwap_filter: !cfg.use_vwap_filter })} />
      </Field>
    </Section>
  </>;
}

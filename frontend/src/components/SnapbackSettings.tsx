import React from 'react';
import {
  useSnapbackConfig,
  useUpdateSnapback,
  type ExitMode,
  type SizingMode,
  type SnapbackConfig,
  type SnapbackTradingMode,
  type StopMode,
} from '../hooks/useSnapback';
import {
  ChoiceRow, Field, NumberField, Section, Switch, DIM,
} from './kite/kiteSettingsPrimitives';
import {
  AdvancedSection, ConfigNote, PanelCard, SettingsDraftBar,
} from './kite/config/ConfigPrimitives';
import { useUnsavedDraftGuard } from './kite/config/unsavedDraftGuard';
import { InstrumentsGroup } from './kite/config/ScanSettings';
import { EnginePowerHeader } from './kite/config/EnginePowerHeader';
import { SnapbackScalpSettings } from './SnapbackScalpSettings';

/**
 * Snapback settings.
 *
 * Same order as every other engine panel — draft bar → power → universe →
 * trigger → contract → exit → risk → advanced — so the settings hub reads as
 * one product.
 *
 * This panel shows the validation record's **current scorecard** rather than a
 * hard-coded verdict. The measured result can change when the research artifact
 * is rerun, so the UI reads the recorded pass count and reasons from the backend.
 * Execution permission is separate and remains fail-closed.
 *
 * The other thing worth knowing before touching a control: two of these
 * defaults look backwards and are not. A 0.70 delta is an IN-the-money option,
 * and a 40-day contract when the trade lasts fifteen sessions. Both follow from
 * the same arithmetic — the edge is a directional drift, so the contract that
 * monetises it best is the one carrying the most delta per rupee of theta.
 * Every such control says so inline, because a number chosen for a reason that
 * lives only in a document gets changed by the next person to open this page.
 */
const EXIT_OPTIONS: Array<{ value: ExitMode; label: string; hint: string }> = [
  { value: 'horizon', label: 'Hold the horizon',
    hint: 'Exit after the set number of days, whatever the price. This is how the strategy was tested.' },
  { value: 'mean_touch', label: 'On the mean',
    hint: 'Exit when price returns to its 20-day average. Not tested.' },
  { value: 'either', label: 'Whichever comes first',
    hint: 'The mean, or the horizon. Also not measured.' },
];

const SIZING_OPTIONS: Array<{ value: SizingMode; label: string; hint: string }> = [
  { value: 'PREMIUM_PCT', label: 'Premium budget',
    hint: 'Spend at most this share of capital on premium. A trade that needs more is skipped.' },
  { value: 'LOTS', label: 'Fixed lots',
    hint: 'Same lots every trade. Note: 2% of ₹1 lakh cannot buy one NIFTY lot.' },
];

const STOP_MODE_OPTIONS: Array<{ value: StopMode; label: string; hint: string }> = [
  { value: 'both', label: 'Broker + monitor',
    hint: 'Best: stop at Zerodha plus Sterling\'s own watch.' },
  { value: 'broker', label: 'Broker only',
    hint: 'Stop at Zerodha only. Works even if Sterling stops.' },
  { value: 'monitor', label: 'Monitor only',
    hint: 'Sterling watches only. If Sterling stops, there is no stop.' },
];

const UNIVERSE_OPTIONS = [
  { value: 'fno' as const, label: 'Whole F&O list',
    hint: 'Every F&O stock and index. Each option must still pass spread, price and OI checks.' },
  { value: 'curated' as const, label: 'Curated fourteen',
    hint: '14 liquid names plus indices. Tighter spreads, very few signals.' },
];

const MARKET_OPTIONS = [
  { value: 'bearish' as const, label: 'Only in a weak market',
    hint: 'Trade only when the index is below its 50-day average. Most important setting: tested -1.28% per day without it, +2.13% with it.' },
  { value: 'off' as const, label: 'Any market',
    hint: 'Take every signal. Measured at -1.28% per entry day.' },
  { value: 'bullish' as const, label: 'Only in a strong market',
    hint: 'Opposite of the above, for comparison. Tested worse.' },
];

const HEDGE_OPTIONS = [
  { value: 'index_futures' as const, label: 'Hedge the market out',
    hint: 'Sell index futures to cancel market risk. Tested +0.52% per day vs -0.62% without. Futures need margin.' },
  { value: 'none' as const, label: 'Directional put',
    hint: 'No hedge. Tested -0.62% per day.' },
];

const CHECK_LABEL: Record<string, string> = {
  enough_trades: 'Enough trades',
  enough_days: 'Enough distinct entry days',
  profitable: 'Profitable out of sample',
  beats_random_timing: 'Beats random entry timing',
  deflated_sharpe: 'Survives the variant count',
  priced_edge: 'Edge bigger than the option costs',
  consistent_across_years: 'Positive every calendar year',
  survivable_drawdown: 'Survivable drawdown',
  mean_proven: 'Size of the edge proven',
};

const ADVANCED_SETTING_COUNT = 13;

/**
 * A switch that says what it switches. The bare `Switch` carries its name only as
 * an accessible label, which leaves two side-by-side switches (weekly / monthly)
 * indistinguishable on screen.
 */
function CaptionedSwitch({ checked, label, onChange }: {
  checked: boolean; label: string; onChange: () => void;
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <Switch checked={checked} label={label} onChange={onChange} />
      <span onClick={onChange} style={{ fontSize: 12, cursor: 'pointer' }}>{label}</span>
    </span>
  );
}
const TRADING_MODES: Array<{ value: SnapbackTradingMode; label: string; hint: string }> = [
  { value: 'ultra_scalping', label: 'Ultra scalp', hint: '1-minute candles. Out within 20 minutes. Never held overnight.' },
  { value: 'scalp', label: 'Scalp', hint: '1-minute candles. Short hold; a strong move can run a little longer.' },
  { value: 'intraday', label: 'Intraday', hint: '15-minute candles. Out by day end.' },
  { value: 'overnight', label: 'Overnight', hint: 'Hourly candles. Up to 3 days. Stop at Zerodha. Never held over weekends or holidays.' },
  { value: 'swing', label: 'Swing', hint: 'Daily candles. Held for days.' },
];

export function SnapbackSettings() {
  const { data, isLoading } = useSnapbackConfig();
  const setCfg = useUpdateSnapback();

  const server = data?.config;
  const defaults = data?.defaults;
  const strategy = data?.strategy;
  const warnings = data?.warnings ?? [];
  const validation = strategy?.validation ?? null;

  const [draft, setDraft] = React.useState<SnapbackConfig | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const cfg = draft ?? server ?? null;
  const dirty = draft != null && server != null
    && (Object.keys(draft) as (keyof SnapbackConfig)[])
      .some((key) => JSON.stringify(draft[key]) !== JSON.stringify(server[key]));

  useUnsavedDraftGuard('snapback', dirty);

  const patch = React.useCallback((next: Partial<SnapbackConfig>) => {
    setDraft((prev) => ({ ...(prev ?? server!), ...next }));
  }, [server]);

  const handleApply = React.useCallback(() => {
    // Only what CHANGED is sent. A full-object write silently reverts whatever
    // moved since the cache was fetched.
    if (!draft || !server) return;
    const changed: Partial<SnapbackConfig> = {};
    (Object.keys(draft) as (keyof SnapbackConfig)[]).forEach((key) => {
      if (JSON.stringify(draft[key]) !== JSON.stringify(server[key])) {
        (changed as Record<string, unknown>)[key] = draft[key];
      }
    });
    setCfg.mutate(changed, {
      onSuccess: () => {
        setDraft(null);
      },
    });
  }, [draft, server, setCfg]);
  const handleDiscard = React.useCallback(() => setDraft(null), []);
  const handleReset = React.useCallback(() => {
    if (!resetConfirm) { setResetConfirm(true); return; }
    if (defaults) setDraft({ ...defaults });
    setResetConfirm(false);
  }, [defaults, resetConfirm]);

  if (isLoading || !cfg || !server || !defaults || !strategy) {
    return (
      <PanelCard>
        <div style={{ color: DIM, fontSize: 12 }}>Loading strategy settings…</div>
      </PanelCard>
    );
  }

  /** True when this default came out of a measurement rather than a judgement. */
  const measured = (key: string) => strategy.calibrated_fields.includes(key);
  const tag = (key: string, hint: string) =>
    (measured(key) ? `${hint} MEASURED.` : `${hint} Not measured — a judgement call.`);
  const shortHorizon = (cfg.trading_mode ?? 'swing') !== 'swing';

  return (
    <>
      <SettingsDraftBar
        dirty={dirty}
        saving={setCfg.isPending}
        onApply={handleApply}
        onDiscard={handleDiscard}
        onReset={handleReset}
        resetConfirm={resetConfirm}
        hasDraft={draft != null}
      />

      <EnginePowerHeader
        name={strategy.name}
        tagline={shortHorizon ? 'Completed-candle reversals with cost-aware targets and runner management.' : strategy.tagline}
        on={cfg.enabled}
        liveOn={server.enabled}
        busy={setCfg.isPending}
        onToggle={() => patch({ enabled: !cfg.enabled })}
        runningNote={shortHorizon
          ? 'Scanning finished option candles. Live trades need ALGO on and all safety checks.'
          : 'Scanning daily bars for instruments stretched past a 20-session extreme.'}
        offNote="Off. Nothing is scanned and no orders can be placed."
      />

      <Section title="Trading mode" description="Pick a style. Each loads its own candle and hold settings."
        summary={TRADING_MODES.find((mode) => mode.value === (cfg.trading_mode ?? 'swing'))?.label ?? 'Daily swing'} defaultOpen persistKey="snapback-mode">
        <Field label="Mode" hint="Scalp and intraday have their own rules. Saving does not start trading.">
          <ChoiceRow value={cfg.trading_mode ?? 'swing'} options={TRADING_MODES} onChange={(mode) => patch({
            trading_mode: mode,
            ...(mode === 'scalp' ? { scalp_timeframe_minutes: 1, scalp_max_hold_bars: 20, scalp_runner_max_bars: 60 } : {}),
            ...(mode === 'ultra_scalping' ? { scalp_timeframe_minutes: 1, scalp_max_hold_bars: 20, scalp_runner_max_bars: 20 } : {}),
            ...(mode === 'intraday' ? { scalp_timeframe_minutes: 15, scalp_max_hold_bars: 16, scalp_runner_max_bars: 24 } : {}),
            ...(mode === 'overnight' ? { scalp_timeframe_minutes: 60, scalp_max_hold_bars: 18, scalp_runner_max_bars: 18 } : {}),
          })} />
        </Field>
      </Section>

      {/* The scorecard, in full. A settings page is read carefully, so the
          evidence belongs here rather than on a hover. */}
      {!shortHorizon && validation && (
        <ConfigNote>
          <span>
            <strong>
              {validation.promoted
                ? 'Validated.'
                : `Passes ${validation.passed} of ${validation.total_checks} gate checks.`}
            </strong>{' '}
            Out of sample: {validation.oos_trades} trades on{' '}
            {validation.oos_entry_days} distinct entry days,{' '}
            {validation.oos_mean_day_return_pct >= 0 ? '+' : ''}
            {validation.oos_mean_day_return_pct.toFixed(2)}% per entry day, Sharpe{' '}
            {validation.sharpe.toFixed(2)}, worst drawdown{' '}
            {validation.max_drawdown_pct.toFixed(1)}% at {validation.allocation_pct}%
            of capital per position. Measured {validation.measured_at} on{' '}
            {validation.span} at {validation.slippage_pct}% slippage per leg.
            <br />
            {Object.entries(validation.checks).map(([key, ok]) => (
              <span key={key} style={{ marginRight: 10 }}>
                {ok ? '✓' : '✗'} {CHECK_LABEL[key] ?? key}
              </span>
            ))}
          </span>
        </ConfigNote>
      )}
      {!shortHorizon && validation?.reasons?.length ? (
        <ConfigNote>
          <span>
            {/* Verbatim from the harness. A summary of a summary is where a
                caveat quietly becomes a slogan. */}
            {validation.reasons.map((r, i) => <div key={i}>· {r}</div>)}
          </span>
        </ConfigNote>
      ) : null}
      {!shortHorizon && <ConfigNote>
        <span>
          Test results use <strong>estimated</strong> option prices, not real ones.
          The trade still pays while options cost up to{' '}
          <strong>{validation?.breakeven_vrp ?? '—'}x</strong> realised volatility. The
          market usually charges {(data.vrp_band ?? [1.15, 1.3]).join('–')}x.
        </span>
      </ConfigNote>}
      {!shortHorizon && <ConfigNote><span>{strategy.provenance}</span></ConfigNote>}

      {warnings.map((w) => <ConfigNote key={w}><span>{w}</span></ConfigNote>)}

      <Section
        title="Instruments"
        description={shortHorizon ? 'What gets scanned, on completed minute bars.' : 'What gets scanned, on daily bars.'}
        summary="Indices and single stocks"
        persistKey="snapback-instruments"
      >
        <Field
          label="Universe"
          hint="Which underlyings are eligible at all."
        >
          <ChoiceRow
            value={cfg.universe_mode}
            options={shortHorizon ? [
              { value: 'fno', label: 'Whole F&O list', hint: 'Scan all F&O names. Each option still needs a good quote and low cost.' },
              { value: 'curated', label: 'Curated fourteen', hint: 'Use the shared registry and indices.' },
            ] : UNIVERSE_OPTIONS}
            onChange={(v) => patch({ universe_mode: v })}
          />
        </Field>
        <NumberField
          label="Universe cap"
          hint="Names per scan. More names = slower scans."
          value={cfg.max_universe}
          defaultValue={defaults.max_universe}
          onChange={(v) => patch({ max_universe: v })}
          min={1} max={500}
          disabled={cfg.universe_mode !== 'fno'}
        />
        <InstrumentsGroup
          indices={cfg.scan_indices}
          stocks={cfg.scan_stocks}
          // This engine has no "all F&O" mode. The measurement has nothing to
          // say about names it never saw, and a thin single-stock option turns
          // the 0.5% modelled slippage into a fiction.
          allStocks={false}
          stockContracts={cfg.scan_stock_contracts}
          onChange={(next) => patch({
            ...(next.scan_indices ? { scan_indices: next.scan_indices } : {}),
            ...(next.scan_stocks ? { scan_stocks: next.scan_stocks } : {}),
            ...(next.scan_stock_contracts != null
              ? { scan_stock_contracts: next.scan_stock_contracts } : {}),
          })}
          idPrefix="snapback"
        />
      </Section>

      {shortHorizon ? <SnapbackScalpSettings cfg={cfg} defaults={defaults} patch={patch} /> : <>
      <Section
        title="What makes it fire"
        description="Price breaks a 20-day high, far from its average, while the market is weak."
        summary="Breakout, stretch and the market gate"
        defaultOpen
        persistKey="snapback-trigger"
      >
        <Field
          label="Market exposure"
          hint={tag('hedge_mode',
            'Keep market risk, or hedge it away with futures.')}
        >
          <ChoiceRow
            value={cfg.hedge_mode}
            options={HEDGE_OPTIONS}
            onChange={(v) => patch({ hedge_mode: v })}
          />
        </Field>
        <Field
          label="Market gate"
          hint={tag('market_filter',
            'Most important setting. Decides when trades are allowed.')}
        >
          <ChoiceRow
            value={cfg.market_filter}
            options={MARKET_OPTIONS}
            onChange={(v) => patch({ market_filter: v })}
          />
        </Field>
        <NumberField
          label="Market mean"
          hint={tag('market_ema',
            'Sessions in the index EMA the gate compares against.')}
          value={cfg.market_ema}
          defaultValue={defaults.market_ema}
          onChange={(v) => patch({ market_ema: v })}
          min={2} max={250} suffix="d"
          disabled={cfg.market_filter === 'off'}
        />
        <NumberField
          label="Breakout lookback"
          hint={tag('lookback_days',
            'Close must break the high of this many days. 20 or 60 both work.')}
          value={cfg.lookback_days}
          defaultValue={defaults.lookback_days}
          onChange={(v) => patch({ lookback_days: v })}
          min={2} max={250} suffix="d"
        />
        <NumberField
          label="Minimum stretch"
          hint={tag('min_stretch_atr',
            'How far above the 20-day average (in ATR). Biggest effect on results.')}
          value={cfg.min_stretch_atr}
          defaultValue={defaults.min_stretch_atr}
          onChange={(v) => patch({ min_stretch_atr: v })}
          min={0} max={10} step={0.1} suffix="ATR"
        />
        <Field
          label="Fade weakness too"
          hint={tag('allow_fade_down',
            'Also buy calls after a 20-day low. Off: call prices are likely understated here.')}
        >
          <Switch
            checked={cfg.allow_fade_down}
            label={cfg.allow_fade_down ? 'Both sides' : 'Puts into strength only'}
            onChange={() => patch({ allow_fade_down: !cfg.allow_fade_down })}
          />
        </Field>
        <NumberField
          label="Cooldown"
          hint={tag('cooldown_days', 'Days to wait before the same name can signal again.')}
          value={cfg.cooldown_days}
          defaultValue={defaults.cooldown_days}
          onChange={(v) => patch({ cooldown_days: v })}
          min={0} max={250} suffix="d"
        />
      </Section>

      <Section
        title="Which contract"
        description="Option is picked by delta."
        summary="Delta, tenor and liquidity"
        persistKey="snapback-contract"
      >
        <NumberField
          label="Target delta"
          hint={tag('target_delta',
            'Option delta. 0.70 = in the money, which suits this strategy best.')}
          value={cfg.target_delta}
          defaultValue={defaults.target_delta}
          onChange={(v) => patch({ target_delta: v })}
          min={0.05} max={0.95} step={0.05}
        />
        <Field
          label="Index expiry cycles"
          hint="Which index expiries to trade. Tested on monthly."
          wide
        >
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
            {(['weekly', 'monthly'] as const).map((series) => {
              const checked = cfg.expiry_series_indices.includes(series);
              return (
                <CaptionedSwitch
                  key={series}
                  checked={checked}
                  label={series === 'weekly' ? 'Weekly' : 'Monthly'}
                  onChange={() => {
                    const next = checked
                      ? cfg.expiry_series_indices.filter((item) => item !== series)
                      : [...cfg.expiry_series_indices, series];
                    if (next.length) patch({ expiry_series_indices: next });
                  }}
                />
              );
            })}
          </div>
        </Field>
        <ConfigNote>
          Stock options are monthly only.
        </ConfigNote>
        <NumberField
          label="Minimum days to expiry"
          hint={tag('min_dte',
            'Shortest expiry allowed. Very short options lose value fast.')}
          value={cfg.min_dte}
          defaultValue={defaults.min_dte}
          onChange={(v) => patch({ min_dte: v })}
          min={1} max={365} suffix="d"
        />
        <NumberField
          label="Maximum days to expiry"
          hint={tag('max_dte', 'Longest expiry allowed. Beyond 45 days adds cost, not benefit.')}
          value={cfg.max_dte}
          defaultValue={defaults.max_dte}
          onChange={(v) => patch({ max_dte: v })}
          min={1} max={365} suffix="d"
        />
        <NumberField
          label="Minimum premium"
          hint={tag('min_option_premium', 'Skip options so cheap that one tick is a big share of the price.')}
          value={cfg.min_option_premium}
          defaultValue={defaults.min_option_premium}
          onChange={(v) => patch({ min_option_premium: v })}
          min={0} max={10000} suffix="₹"
        />
        <NumberField
          label="Maximum spread"
          hint={tag('max_spread_pct', 'Expected slippage, split across buy and sell. Works up to 2%.')}
          value={cfg.max_spread_pct}
          defaultValue={defaults.max_spread_pct}
          onChange={(v) => patch({ max_spread_pct: v })}
          min={0} max={100} step={0.1} suffix="%"
        />
      </Section>

      <Section
        title="How it ends"
        description="The horizon is the measurement; everything else is damage control."
        summary="Horizon and premium stop"
        persistKey="snapback-exit"
      >
        <Field
          label="Exit rule"
          hint="Position management is damage control on an edge you already
                have — it reshapes the distribution and cannot add drift."
        >
          <ChoiceRow
            value={cfg.exit_mode}
            options={EXIT_OPTIONS}
            onChange={(v) => patch({ exit_mode: v })}
          />
        </Field>
        <NumberField
          label="Holding period"
          hint={tag('hold_days',
            'Days to hold. Best tested at 10. Must be shorter than days to expiry.')}
          value={cfg.hold_days}
          defaultValue={defaults.hold_days}
          onChange={(v) => patch({ hold_days: v })}
          min={1} max={60} suffix="d"
        />
        <NumberField
          label="Premium stop"
          hint={tag('premium_stop_pct', 'Exit if the option loses this share of what you paid.')}
          value={cfg.premium_stop_pct}
          defaultValue={defaults.premium_stop_pct}
          onChange={(v) => patch({ premium_stop_pct: v })}
          min={1} max={100} suffix="%"
        />
      </Section>

      <Section
        title="Size and protection"
        description="What one signal deploys, and what protects it."
        summary="Premium budget and where the stop lives"
        persistKey="snapback-risk"
      >
        <Field label="Sizing" hint="How many lots a signal buys.">
          <ChoiceRow
            value={cfg.sizing_mode}
            options={SIZING_OPTIONS}
            onChange={(v) => patch({ sizing_mode: v })}
          />
        </Field>
        <NumberField
          label="Premium per position"
          hint={tag('premium_pct_of_capital', 'Share of capital per trade. Most you can lose is the premium.')}
          value={cfg.premium_pct_of_capital}
          defaultValue={defaults.premium_pct_of_capital}
          onChange={(v) => patch({ premium_pct_of_capital: v })}
          min={0.01} max={100} step={0.5} suffix="%"
          disabled={cfg.sizing_mode !== 'PREMIUM_PCT'}
        />
        <NumberField
          label="Capital"
          hint={tag('capital_inr', 'What the premium budget is a percentage of.')}
          value={cfg.capital_inr}
          defaultValue={defaults.capital_inr}
          onChange={(v) => patch({ capital_inr: v })}
          min={1000} max={1e10} step={10000} suffix="₹"
        />
        <NumberField
          label="Maximum lots"
          hint={tag('max_lots', 'Ceiling per position, whichever sizing mode is chosen.')}
          value={cfg.max_lots}
          defaultValue={defaults.max_lots}
          onChange={(v) => patch({ max_lots: v })}
          min={1} max={100}
        />
        <NumberField
          label="Maximum open positions"
          hint={tag('max_open_positions', 'Most open trades at once. Signals bunch on the same day.')}
          value={cfg.max_open_positions}
          defaultValue={defaults.max_open_positions}
          onChange={(v) => patch({ max_open_positions: v })}
          min={1} max={50}
        />
        <Field label="Stop lives at" hint="Snapback only enters with a confirmed stop at Zerodha.">
          <ChoiceRow
            value={cfg.stop_mode}
            options={STOP_MODE_OPTIONS}
            onChange={(v) => patch({ stop_mode: v })}
          />
        </Field>
      </Section>

      <AdvancedSection count={ADVANCED_SETTING_COUNT}>
        <NumberField
          label="Assumed vol premium"
          hint={tag('assumed_vrp', 'Assumed option price when no live quote exists (× realised volatility).')}
          value={cfg.assumed_vrp}
          defaultValue={defaults.assumed_vrp}
          onChange={(v) => patch({ assumed_vrp: v })}
          min={0.5} max={3} step={0.01} suffix="x"
        />
        <NumberField
          label="Put skew"
          hint={tag('smile_slope',
            'Price adjustment for options below spot. 0 = simple model.')}
          value={cfg.smile_slope}
          defaultValue={defaults.smile_slope}
          onChange={(v) => patch({ smile_slope: v })}
          min={0} max={6} step={0.1}
        />
        <NumberField
          label="Skew above spot"
          hint={tag('smile_itm_slope',
            'Price adjustment for in-the-money options.')}
          value={cfg.smile_itm_slope}
          defaultValue={defaults.smile_itm_slope}
          onChange={(v) => patch({ smile_itm_slope: v })}
          min={0} max={6} step={0.1}
        />
        <NumberField
          label="Sold leg delta"
          hint={tag('short_leg_delta',
            'Also sell a cheaper option to make a spread. 0 = off (tested best).')}
          value={cfg.short_leg_delta}
          defaultValue={defaults.short_leg_delta}
          onChange={(v) => patch({ short_leg_delta: v })}
          min={0} max={0.9} step={0.05}
        />
        <NumberField
          label="Maximum realised-vol percentile"
          hint={tag('max_rv_pct', 'Skip trades when volatility is unusually high. 70 = tested. 100 = off.')}
          value={cfg.max_rv_pct}
          defaultValue={defaults.max_rv_pct}
          onChange={(v) => patch({ max_rv_pct: v })}
          min={1} max={100} step={1} suffix="%ile"
        />
        <NumberField
          label="Realised-vol window"
          hint={tag('rv_window', 'Sessions behind the modelled premium and the strike pick.')}
          value={cfg.rv_window}
          defaultValue={defaults.rv_window}
          onChange={(v) => patch({ rv_window: v })}
          min={5} max={250} suffix="d"
        />
        <NumberField
          label="Mean window"
          hint={tag('mean_touch_ema', 'Average used to measure the stretch.')}
          value={cfg.mean_touch_ema}
          defaultValue={defaults.mean_touch_ema}
          onChange={(v) => patch({ mean_touch_ema: v })}
          min={2} max={250} suffix="d"
        />
        <NumberField
          label="Exit after regime flip"
          hint={tag('exit_on_regime_flip', 'Exit after the market filter fails this many days. 0 = off (tested).')}
          value={cfg.exit_on_regime_flip}
          defaultValue={defaults.exit_on_regime_flip}
          onChange={(v) => patch({ exit_on_regime_flip: v })}
          min={0} max={60} step={1} suffix="d"
        />
        <NumberField
          label="Premium trail"
          hint={tag('premium_trail_pct', 'Trailing stop on the best price. 0 = off (tested).')}
          value={cfg.premium_trail_pct}
          defaultValue={defaults.premium_trail_pct}
          onChange={(v) => patch({ premium_trail_pct: v })}
          min={0} max={100} suffix="%"
        />
        <NumberField
          label="Let winners run"
          hint={tag('runner_mult', 'Keep holding past the end date while worth this many times the cost. 0 = off.')}
          value={cfg.runner_mult}
          defaultValue={defaults.runner_mult}
          onChange={(v) => patch({ runner_mult: v })}
          min={0} max={20} step={0.1} suffix="x"
        />
        <NumberField
          label="Runner give-back"
          hint={tag('runner_trail_pct', 'When held longer, exit if price drops this much from its best.')}
          value={cfg.runner_trail_pct}
          defaultValue={defaults.runner_trail_pct}
          onChange={(v) => patch({ runner_trail_pct: v })}
          min={0} max={100} suffix="%"
        />
        <NumberField
          label="Minimum ATR"
          hint={tag('min_atr_bp', 'Skip names that move too little to cover costs. 0 = off.')}
          value={cfg.min_atr_bp}
          defaultValue={defaults.min_atr_bp}
          onChange={(v) => patch({ min_atr_bp: v })}
          min={0} max={10000} suffix="bp"
        />
        <Field
          label="One position per instrument"
          hint="Two fades of one instrument are one bet with two tickets."
        >
          <Switch
            checked={cfg.one_position_per_underlying}
            label={cfg.one_position_per_underlying ? 'Enforced' : 'Allowed to stack'}
            onChange={() => patch({
              one_position_per_underlying: !cfg.one_position_per_underlying,
            })}
          />
        </Field>
      </AdvancedSection>
      </>}
    </>
  );
}

export default SnapbackSettings;

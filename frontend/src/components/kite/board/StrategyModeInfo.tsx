import type { EngineConfigModel } from '../../../types/kiteEngine';
import type { SnapbackConfig } from '../../../hooks/useSnapback';
import { k } from '../../../styles/kiteUI';

const MODES = [
  ['ultra_scalping', 'Ultra scalp', 1, '1', '20 minutes; same-day exit'],
  ['scalping', 'Scalp', 5, '1 / 3 / 5', '90 minutes; same-day exit'],
  ['intraday', 'Intraday', 15, '5 / 15 / 30', 'Same-day exit, 15:20 IST'],
  ['overnight', 'Overnight', 60, '30 / 60', 'Up to 3 trading sessions'],
  ['swing', 'Swing', 60, '60', 'Up to 15 trading sessions'],
] as const;

/** Read-only facts from the displayed draft or cached server config. Never saves settings. */
const clock = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

export function StrategyModeInfo({ engine, st, sb, bounded = false }: {
  engine: string; bounded?: boolean; st?: Partial<EngineConfigModel> | null; sb?: Partial<SnapbackConfig> | null;
}) {
  if (!['supertrend', 'snapback', 'gamma_move', 'ema_trend'].includes(engine)) return null;
  const original = engine === 'gamma_move' || engine === 'ema_trend';
  const supertrend = engine === 'supertrend';
  const mode = supertrend ? st?.horizon_mode ?? 'swing' : sb?.trading_mode ?? 'swing';
  const row = MODES.find(item => item[0] === (mode === 'scalp' ? 'scalping' : mode))!;
  const loaded = supertrend ? st != null : sb != null;
  const tf = supertrend ? st?.signal_timeframe_minutes ?? row[2] : sb?.scalp_timeframe_minutes ?? (mode === 'intraday' ? 15 : mode === 'overnight' ? 60 : 1);
  const candles = supertrend ? `${tf}-minute Heikin-Ashi` : mode === 'swing' ? 'Daily regular OHLC' : `${tf}-minute regular OHLC`;
  const facts = <>
      {loaded && supertrend && <div>Three SuperTrends: (21,1), (14,2), (7,3). Source: {st?.scan_source ?? 'not supplied'}. Exit counter: {st?.exit_mode?.replace(/_/g, ' ') ?? 'not supplied'}; trail: {st?.trail_target ?? 'not supplied'}. Price-stop exit: {st?.price_stop_exit === false ? 'off' : 'on'}. No native fixed target.</div>}
      {loaded && !supertrend && (mode === 'swing'
        ? <div>Daily stretch reversal · lookback {sb?.lookback_days ?? 20} sessions · stretch {sb?.min_stretch_atr ?? 1.5} ATR. Premium stop {sb?.premium_stop_pct ?? 35}%; exit {sb?.exit_mode ?? 'horizon'}; hold {sb?.hold_days ?? 15} sessions.</div>
        : <div>Completed option-candle reversal · Bollinger (20,2). Target {sb?.scalp_target_points ?? 5} pts; stop {sb?.scalp_stop_points ?? 4} pts; runner trail {sb?.scalp_trail_points ?? 2} pts; lock {sb?.scalp_lock_points ?? 1} pts (option premium). Initial hold {sb?.scalp_max_hold_bars ?? (mode === 'intraday' ? 16 : mode === 'overnight' ? 18 : 20)} bars; runner {sb?.scalp_runner_max_bars ?? (mode === 'ultra_scalping' ? 20 : mode === 'intraday' ? 24 : mode === 'overnight' ? 18 : 60)} bars. Costs can raise the requested target. EMA confirmation {sb?.use_ema_confirmation ? 'on' : 'off'}; ADX filter {sb?.use_adx_filter ? 'on' : 'off'}.</div>)}
      {loaded && <div>{supertrend ? `Holding ceiling: ${row[4]}. Session square-off ceiling 15:20 IST; the holding deadline can bind earlier.` : mode === 'swing' ? 'Minute-mode candle and runner settings are inactive in daily Swing.' : `Entry window ${clock(sb?.scalp_entry_start_minute ?? 575)}–${clock(sb?.scalp_entry_end_minute ?? 885)} IST; square-off ${clock(sb?.scalp_square_off_minute ?? 915)} IST${mode === 'overnight' ? ' on the final holding session; up to 3 sessions' : ' same day'}.`}</div>}
  </>;
  return <aside aria-label="Strategy mode configuration" tabIndex={bounded ? 0 : undefined} style={{ maxHeight: bounded ? '40%' : undefined, overflowY: bounded ? 'auto' : undefined, boxSizing: 'border-box', padding: '8px 12px', borderBottom: `1px solid ${k.border}`, color: k.text, fontSize: 11.5, lineHeight: 1.5, flexShrink: 0, overflowWrap: 'anywhere' }}>
    {original ? <>
      <strong>Default · Original only</strong>
      <div>{engine === 'ema_trend'
        ? 'Daily signals · Weekly context · EMA 10/21, DEMA 50, SuperTrend (10,3). Practice only. No add-on modes.'
        : '5-minute completed E2 bars → live premium E1 breakout. 15-minute chart display is separate from signal timing. Expiry within 1 trading day: Intraday; otherwise Swing. No add-on modes.'}</div>
    </> : <>
      <div><strong>Default: Swing · original · {supertrend ? '60-minute Heikin-Ashi' : 'Daily regular OHLC'}</strong></div>
      <div>{loaded ? `Selected: ${row[1]} · ${candles}` : 'Selected configuration unavailable — waiting for settings.'}</div>
      {!bounded && facts}
      <div>Board mode filters only change rows shown. Settings determine candle size; each row or held position may retain its own mode.</div>
      <details>
        <summary style={{ cursor: 'pointer' }}>Compare modes and configuration</summary>
        {bounded && facts}
        <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
          <caption style={{ textAlign: 'left' }}>Presets below; the selected configuration above includes overrides. Default identifies the original strategy, not the active selection.</caption>
          <thead><tr>{['Mode', 'Preset candles', 'Allowed minutes', 'Holding limit'].map(label => <th key={label} style={{ padding: 4 }}>{label}</th>)}</tr></thead>
          <tbody>{MODES.map(([value, label, minutes, allowed, limit]) => <tr key={value}>
            <th scope="row" style={{ padding: 4 }}>{label}{value === 'swing' ? ' · Default / Original' : ''}</th>
            <td style={{ padding: 4 }}>{supertrend ? `${minutes}m Heikin-Ashi` : value === 'swing' ? 'Daily OHLC' : `${value === 'scalping' ? 1 : minutes}m OHLC`}</td>
            <td style={{ padding: 4 }}>{!supertrend && value === 'swing' ? 'Daily only' : allowed}</td>
            <td style={{ padding: 4 }}>{supertrend ? limit : value === 'swing' ? 'Configured daily hold (default 15 sessions)' : value === 'overnight' ? 'Up to 3 sessions; broker protection required' : value === 'ultra_scalping' ? '20-bar preset; same-day exit' : value === 'scalping' ? '20 bars; runner 60 bars; same-day exit' : '16 bars; runner 24 bars; same-day exit'}</td>
          </tr>)}</tbody>
        </table></div>
        <p>{supertrend ? 'All modes retain the three SuperTrends and Heikin-Ashi transformation. Ultra scalp uses 1-minute candles, not 1-hour candles. Raw market prices determine stop touches. Hourly evidence does not validate faster modes.' : 'Snapback uses regular OHLC, not Heikin-Ashi or triple SuperTrend. Swing uses daily stretch rules; other modes use completed-bar Bollinger reversals. Daily research does not validate short modes.'} Stops, expiry and protection may exit earlier. Existing positions retain their frozen entry policy.</p>
        <p>Settings readouts follow your draft until Apply. Board readouts show saved settings; each row and open position may carry its own mode and frozen configuration.</p>
        <p>Board MODES filters only hide or show rows. Change trading configuration in Settings and Apply; viewing this comparison does not save anything.</p>
      </details>
    </>}
  </aside>;
}

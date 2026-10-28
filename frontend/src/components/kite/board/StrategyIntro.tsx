/** One consistent, mobile-first strategy explanation.
 *
 * All engines share this shell: no big prose cards above the first signal row
 * on phones. Desktop still exposes the existing read-only explanations.
 * Expanding information never starts scans, changes settings or places orders.
 */
import { useId, useState } from 'react';
import type { EngineConfigModel } from '../../../types/kiteEngine';
import type { SnapbackConfig } from '../../../hooks/useSnapback';
import { StrategyModeInfo } from './StrategyModeInfo';
import { StrategyGuide } from './StrategyGuide';

const MODE_NAMES: Record<string, string> = {
  swing: 'Swing', overnight: 'Overnight', intraday: 'Intraday',
  scalp: 'Scalp', scalping: 'Scalp', ultra_scalping: 'Ultra scalp',
};
const ENGINE_SUMMARIES: Record<string, string> = {
  gamma_move: 'Gamma Unwind · Original only · 5m E2 / live E1 breakout',
  ema_trend: 'EMA Trend · Practice only · Daily/weekly · EMA 10/21 + DEMA 50',
  adaptive_edge: 'Adaptive Edge · Practice only · Order-flow research',
  intraday: 'Intraday · Same-day index-option signals',
  navigator: 'Navigator · Scanner-selected option ideas',
  price_rules: 'Price Rules · Personal entry plans',
};

export function strategyIntroSummary(
  engine: string, st?: Partial<EngineConfigModel> | null,
  sb?: Partial<SnapbackConfig> | null,
): string {
  if (engine === 'supertrend') {
    if (!st) return 'SuperTrend · Default Swing (60m Heikin-Ashi) · saved settings loading';
    const mode = st.horizon_mode ?? 'swing';
    const selected = MODE_NAMES[mode] ?? mode.replace(/_/g, ' ');
    const minutes = st?.signal_timeframe_minutes
      ?? (mode === 'ultra_scalping' ? 1 : mode === 'scalping' ? 5
        : mode === 'intraday' ? 15 : 60);
    return `SuperTrend · Default Swing · ${selected} selected · ${minutes}m Heikin-Ashi`;
  }
  if (engine === 'snapback') {
    if (!sb) return 'Snapback · Default Swing (Daily OHLC) · saved settings loading';
    const mode = sb.trading_mode ?? 'swing';
    const selected = MODE_NAMES[mode] ?? mode.replace(/_/g, ' ');
    const tf = mode === 'swing' ? 'Daily OHLC'
      : `${sb?.scalp_timeframe_minutes ?? (mode === 'intraday' ? 15 : mode === 'overnight' ? 60 : 1)}m OHLC`;
    return `Snapback · Default Swing · ${selected} selected · ${tf}`;
  }
  return ENGINE_SUMMARIES[engine] ?? `${engine.replace(/_/g, ' ')} · Strategy information`;
}

export function StrategyIntro({ engine, st, sb }: {
  engine: string;
  st?: Partial<EngineConfigModel> | null;
  sb?: Partial<SnapbackConfig> | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const summary = strategyIntroSummary(engine, st, sb);
  return <section className={`strategy-mobile-overview${expanded ? ' is-expanded' : ''}`}
    data-strategy={engine} aria-label="Strategy overview">
    <button type="button" className="strategy-mobile-overview-trigger"
      aria-controls={id} aria-expanded={expanded}
      onClick={() => setExpanded(value => !value)}
      title={summary}>
      <span className="strategy-mobile-overview-title">{summary}</span>
      <span className="strategy-mobile-overview-chevron" aria-hidden="true">{expanded ? '▴' : '▾'}</span>
    </button>
    <div id={id} className="strategy-mobile-overview-content">
      <StrategyModeInfo bounded engine={engine} st={st} sb={sb} />
      <StrategyGuide engine={engine} />
      {engine === 'price_rules' && <p className="strategy-mobile-overview-fallback">
        Published and armed plans are listed below. Opening this explanation never arms or triggers an entry.
      </p>}
    </div>
  </section>;
}

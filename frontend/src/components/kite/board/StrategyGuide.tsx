/**
 * A plain-English line under the strategy tabs: what the strategy does, and the three steps a new
 * user needs (look, buy, or let it trade). Hidden per strategy once the user taps "Got it".
 */
import { useState } from 'react';
import { k } from '../../../styles/kiteUI';
import type { EngineId } from './boardTypes';

const WHAT: Partial<Record<EngineId | 'price_rules', string>> = {
  supertrend: 'Buys a call option when three trend lines turn up together. Sells when they turn down or the trailing stop is hit.',
  gamma_move: 'Watches options whose sellers are leaving. Buys when the option price breaks above its recent range. Takes profit at +20% and +40%.',
  snapback: 'Finds stocks that moved too far, too fast. Buys an option that gains when the price comes back toward its average.',
  ema_trend: 'Practice only, no real orders. Follows stocks trending above their 10- and 21-day averages.',
  intraday: 'Same-day trades on index options. Every position is closed before the market shuts.',
  navigator: 'Trade ideas from the Navigator scanner, with the option already picked for you.',
  adaptive_edge: 'Short option scalps from order flow. For watching and learning; it does not place orders.',
  price_rules: 'Your own price rules: buy when a price you set is reached.',
};

const storageKey = (engine: string) => `sterling.strategy-guide.hidden.${engine}`;
const readHidden = (engine: string) => {
  try { return localStorage.getItem(storageKey(engine)) === '1'; } catch { return false; }
};

export function StrategyGuide({ engine }: { engine: string }) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const what = WHAT[engine as EngineId];
  if (!what || hidden[engine] || readHidden(engine)) return null;
  const hide = () => {
    try { localStorage.setItem(storageKey(engine), '1'); } catch { /* storage blocked: hide for this visit */ }
    setHidden(h => ({ ...h, [engine]: true }));
  };
  return (
    <div role="note" aria-label="How this strategy works" style={{
      display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 12px', flexShrink: 0,
      borderBottom: `1px solid ${k.border}`, background: 'var(--k-bg-2, transparent)', fontSize: 11.5, lineHeight: 1.45, color: k.text,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div>{what}</div>
        <div style={{ color: k.dim, marginTop: 3 }}>
          <strong style={{ color: k.text }}>1</strong> Tap a row to see the plan ·{' '}
          <strong style={{ color: k.text }}>2</strong> Press <strong style={{ color: k.green }}>B</strong> to buy it yourself ·{' '}
          <strong style={{ color: k.text }}>3</strong> To let Sterling trade for you: Connect → Trading mode → set this strategy to ALGO (auto).
        </div>
      </div>
      <button type="button" onClick={hide} aria-label="Hide this guide" style={{
        flexShrink: 0, border: `1px solid ${k.border}`, borderRadius: 4, background: 'transparent', color: k.dim,
        fontSize: 10.5, padding: '3px 8px', cursor: 'pointer', fontFamily: 'inherit',
      }}>Got it</button>
    </div>
  );
}

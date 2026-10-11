import React from 'react';
import { parseInstrument, formatInstrument } from '../../lib/instrument';
export { parseInstrument, formatInstrument } from '../../lib/instrument';
export type { ParsedParts } from '../../lib/instrument';

export function getOrdinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}

export function InstrumentLabel({ symbol, fallback, onColor }: { symbol: string; fallback?: string; onColor?: string }) {
  const parts = symbol.split(':');
  let exchange = parts.length > 1 ? parts[0] : '';
  const rawTs = parts.length > 1 ? parts[1] : symbol;
  // On a colored header (onColor set), the default blue weekly badge is invisible —
  // render it as a light chip with the accent letter instead.
  const wkColor = onColor || 'var(--color-text-4, var(--k-blue))';
  const wkBg = onColor ? 'color-mix(in srgb, var(--k-bg) 92%, transparent)' : 'rgba(var(--color-bg-5--rgb, 65, 132, 243), 0.1)';
  const wkBase: React.CSSProperties = {
    color: wkColor, backgroundColor: wkBg, textAlign: 'center', borderRadius: '100%',
    width: 11, height: 11, fontSize: '0.62em', lineHeight: '11px', display: 'inline-block', fontWeight: 700,
  };

  if (rawTs === 'NIFTY 50' || rawTs === 'NIFTY BANK' || rawTs === 'SENSEX' || rawTs === 'BANKEX' || rawTs === 'NIFTY 100' || rawTs === 'NIFTY COMMODITIES' || rawTs === 'NIFTY FIN SERVICE' || rawTs.includes('INDEX')) {
    exchange = 'INDEX';
    exchange = '';
  }

  const parsed = parseInstrument(rawTs);

  if (!parsed) {
    // Never let the generic instrument `name` (for example BAJAJ-AUTO) hide a more
    // specific tradingsymbol. Search/watch rows must retain expiry, strike and side.
    const display = rawTs || fallback || '';
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
        <span>{display.replace(/(CE|PE)(BFO|NFO)?$/, ' $1')}</span>
        {exchange && <span style={{ fontSize: 9, color: 'var(--k-dim)', marginLeft: 4 }}>{exchange}</span>}
      </span>
    );
  }

  const { underlying, day, month, strike, type, isWeekly } = parsed;

  return (
    <span aria-label={formatInstrument(symbol)} style={{ display: 'inline-flex', alignItems: 'baseline', whiteSpace: 'nowrap' }}>
      <span style={{ marginRight: 4 }}>{underlying}</span>
      <span style={{ marginRight: 4, display: 'inline-flex', alignItems: 'baseline', gap: 3, fontSize: 12 }}>
        {day && (
          <span>
            {day}
            <sup style={{ fontSize: '0.85em', marginLeft: 1 }}>
              {getOrdinal(day)}
              {isWeekly && <span style={{ ...wkBase, marginLeft: 4 }}>w</span>}
            </sup>
          </span>
        )}
        {!day && isWeekly && <span style={{ ...wkBase, marginLeft: 2, marginRight: 2 }}>w</span>}
        {month && <span>{month}</span>}
      </span>
      {strike && <span style={{ marginRight: 4 }}>{strike}</span>}
      {type && <span style={{ marginRight: 4 }}>{type}</span>}
      {exchange && !type && exchange !== 'INDEX' && <span style={{ fontSize: 9, color: 'var(--k-dim)', background: 'var(--k-surface-hover)', padding: '1px 4px', borderRadius: 2 }}>{exchange}</span>}
    </span>
  );
}

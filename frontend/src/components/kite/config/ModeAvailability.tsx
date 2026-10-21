import React from 'react';
import { MODE_LABEL, TRADE_MODES, type TradeMode } from '../board/boardTypes';

/**
 * The five trading modes, shown the same way on every strategy's settings.
 *
 * A mode the strategy was never built and tested for reads "Not available":
 * it cannot trade until it has its own rules and test results. Inventing rules
 * for an untested mode would put real money on guesswork.
 */
export function ModeAvailability({ available, active, note }: {
  available: readonly TradeMode[];
  /** Modes switched on now; defaults to all available ones. */
  active?: readonly TradeMode[];
  note?: string;
}) {
  const on = new Set(active ?? available);
  return (
    <div role="group" aria-label="Trading modes" style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {TRADE_MODES.map((mode) => {
          const ok = available.includes(mode);
          const live = ok && on.has(mode);
          return (
            <span key={mode} data-mode={mode} aria-disabled={!ok}
              title={ok ? (live ? 'On' : 'Available, off') : 'Not available: not built or tested for this strategy'}
              style={{
                display: 'inline-flex', flexDirection: 'column', minWidth: 78, padding: '5px 9px', borderRadius: 6,
                border: `1px solid ${live ? 'var(--k-brand)' : 'var(--k-border)'}`,
                color: ok ? 'var(--k-text)' : 'var(--k-dim)', opacity: ok ? 1 : 0.65, fontSize: 11.5,
              }}>
              <strong style={{ fontWeight: 600 }}>{MODE_LABEL[mode]}</strong>
              <span style={{ fontSize: 10, color: 'var(--k-dim)' }}>{ok ? (live ? 'On' : 'Off') : 'Not available'}</span>
            </span>
          );
        })}
      </div>
      {note && <div style={{ fontSize: 10.5, color: 'var(--k-dim)', marginTop: 6 }}>{note}</div>}
    </div>
  );
}

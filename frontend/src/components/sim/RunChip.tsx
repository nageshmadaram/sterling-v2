import type { SimRunState } from '../../hooks/useSim';
import { SIM } from './simStyle';

const TONE = {
  good: SIM.live,
  warn: 'var(--k-warn-deep, #e65100)',
  bad: SIM.danger,
  quiet: SIM.muted,
} as const;

/** The running / stalled indicator: a word, a colour and a dot, never colour alone. */
export function RunChip({ state }: { state: SimRunState }) {
  const color = TONE[state.tone];
  return (
    <span
      role="status"
      data-testid="sim-run-chip"
      data-state={state.key}
      title={state.detail}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 999,
        border: `1px solid ${color}`, color, background: SIM.surface,
        fontSize: 11, fontWeight: 700, letterSpacing: 0.3, whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
      {state.label}
    </span>
  );
}

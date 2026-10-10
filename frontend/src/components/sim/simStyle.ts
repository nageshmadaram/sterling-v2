import type { CSSProperties } from 'react';

/**
 * Colours for the Simulation surfaces. Amber means "virtual money" everywhere it appears, blue
 * is for information about it, and the real account keeps its existing green. All of them are
 * theme tokens, so light and dark follow the app.
 */
export const SIM = {
  accent: 'var(--k-amber-2)',
  tint: 'var(--k-tint-amber)',
  ink: 'var(--k-warn-ink)',
  info: 'var(--k-blue-kite)',
  infoTint: 'var(--k-tint-blue)',
  live: 'var(--k-green-deep, var(--k-green))',
  danger: 'var(--k-red-brick, var(--k-red))',
  text: 'var(--k-text)',
  muted: 'var(--k-ink-5)',
  faint: 'var(--k-ink-6)',
  border: 'var(--k-border)',
  surface: 'var(--k-bg)',
  sunken: 'var(--k-surface-sunken-2)',
} as const;

/** Touch-friendly minimum for every control in these surfaces. */
export const TAP = 36;

export const button = (accent: string, filled = false): CSSProperties => ({
  minHeight: TAP,
  padding: '0 14px',
  borderRadius: 7,
  border: `1px solid ${accent}`,
  background: filled ? accent : SIM.surface,
  color: filled ? 'var(--k-on-accent)' : accent,
  fontFamily: 'inherit',
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
});

export const srOnly: CSSProperties = {
  position: 'absolute', width: 1, height: 1, margin: -1, padding: 0, overflow: 'hidden',
  clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0,
};

import type { CSSProperties } from 'react';
import { useFeedHealth } from '../../hooks/useFeedHealth';

/**
 * Says so when the screen is not live.
 *
 * Shown only when something is wrong -- a healthy or idle feed renders nothing --
 * because a permanent "LIVE" chip trains the eye to ignore the one that matters.
 * The text states the consequence (data on screen is the last fetched, not live),
 * not just the symptom.
 */
export function LiveFeedBadge() {
  const health = useFeedHealth();
  if (health.level === 'idle' || health.level === 'live' || health.level === 'connecting') {
    return null;
  }
  const color = health.level === 'stale' ? 'var(--k-amber, #e0a000)' : 'var(--k-red-strong, #d9534f)';
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="live-feed-badge"
      data-level={health.level}
      title={health.detail}
      style={{ ...wrap, borderColor: color }}
    >
      <span aria-hidden style={{ ...dot, background: color }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ ...label, color }}>{health.label}</div>
        <div style={detail}>{health.detail}</div>
      </div>
    </div>
  );
}

// Above `.term-root` (z-index 10000) so the app's stacking contexts cannot hide it.
const wrap: CSSProperties = {
  position: 'fixed', left: 12, bottom: 12, zIndex: 10001, maxWidth: 340,
  display: 'flex', gap: 10, alignItems: 'flex-start', padding: '8px 12px',
  borderRadius: 8, border: '1px solid', pointerEvents: 'none',
  background: 'var(--k-bg, #13161c)', color: 'var(--k-text, #e6e8ee)',
  boxShadow: '0 6px 22px rgba(0,0,0,0.28)',
};
const dot: CSSProperties = { width: 8, height: 8, borderRadius: '50%', marginTop: 5, flexShrink: 0 };
const label: CSSProperties = { fontSize: 11, fontWeight: 800, letterSpacing: 0.6 };
const detail: CSSProperties = { fontSize: 11, lineHeight: 1.35, opacity: 0.85, marginTop: 2 };

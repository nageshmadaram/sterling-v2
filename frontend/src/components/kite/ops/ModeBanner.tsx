/**
 * The one line an operator must not be able to misread.
 *
 * The mode comes from the backend. The browser never infers it from a single
 * switch: that switch is one of several conditions, and a screen that guessed the
 * mode from it would report a healthy state during a recovery.
 */
import type { ProductionMode } from '../../../types/liveOperations';

const BANNERS: Record<ProductionMode, { text: string; tone: 'ok' | 'warn' | 'err' }> = {
  LIVE: { text: 'LIVE — TRADING ENABLED', tone: 'ok' },
  LIVE_HALTED: { text: 'LIVE — NEW ENTRIES OFF (KILL SWITCH)', tone: 'warn' },
  RECOVERY_REQUIRED: { text: 'RECOVERY REQUIRED — NEW RISK BLOCKED', tone: 'err' },
  DEVELOPMENT: { text: 'DEVELOPMENT — NOT A PRODUCTION DEPLOYMENT', tone: 'warn' },
  UNKNOWN: { text: 'SYSTEM MODE UNKNOWN — TREAT AS BLOCKED', tone: 'err' },
};

const TONES = {
  ok: { fg: 'var(--k-ok, #10b981)', bg: 'rgba(16,185,129,0.10)' },
  warn: { fg: 'var(--k-warn, #f59e0b)', bg: 'rgba(245,158,11,0.10)' },
  err: { fg: 'var(--k-err, #ef4444)', bg: 'rgba(239,68,68,0.12)' },
};

export function ModeBanner({
  mode,
  unreachable,
}: {
  mode: ProductionMode | string;
  unreachable?: boolean;
}) {
  const banner = unreachable
    ? { text: 'STERLING UNREACHABLE — STATE UNKNOWN, TREAT AS BLOCKED', tone: 'err' as const }
    : BANNERS[mode as ProductionMode] ?? BANNERS.UNKNOWN;
  const tone = TONES[banner.tone];

  return (
    <div
      role="status"
      aria-label="trading mode"
      aria-live="polite"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 5,
        padding: '6px 10px',
        marginBottom: 10,
        border: `1px solid ${tone.fg}`,
        background: tone.bg,
        color: tone.fg,
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: 0.6,
        textAlign: 'center',
      }}
    >
      {banner.text}
    </div>
  );
}

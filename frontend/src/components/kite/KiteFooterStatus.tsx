import React from 'react';
import { fmtTimeIST } from '../../lib/format';
import { k } from '../../styles/kiteUI';
import { useKiteStatus } from '../../hooks/useKite';
import {
  recoveryLabel,
  recoveryNeedsAttention,
  safeModeEngaged,
  useLiveStatus,
} from '../../hooks/useLiveStatus';
import { openSettingsSection } from './config/registry';

type Tone = 'good' | 'warn' | 'bad';
const TONES: Record<Tone, { fg: string; border: string; bg: string }> = {
  good: { fg: k.green, border: 'rgba(34, 197, 94, 0.45)', bg: 'rgba(34, 197, 94, 0.12)' },
  warn: { fg: k.orange, border: 'rgba(245, 158, 11, 0.45)', bg: 'rgba(245, 158, 11, 0.12)' },
  bad: { fg: k.red, border: 'rgba(239, 68, 68, 0.45)', bg: 'rgba(239, 68, 68, 0.12)' },
};

/**
 * What blocks new entries, in the footer so it is visible on every page. The detail and
 * the Resume action live in Trading Config. Nothing renders when the status endpoint is
 * unavailable: an invented "LIVE" would be worse than a blank.
 */
function LiveStateChip() {
  const live = useLiveStatus().data;
  const status = live?.available ? live.status : null;
  if (!status) return null;

  const engaged = safeModeEngaged(status);
  let tone: Tone = 'good';
  let text = 'LIVE';
  let hint = 'Live trading. New entries allowed.';
  if (engaged === true) {
    tone = 'bad';
    text = 'SAFE MODE';
    hint = `Safe mode: new entries blocked${status.safeMode.reason ? ` (${status.safeMode.reason})` : ''}. Open Trading Config to resume.`;
  } else if (status.liveExecution === false) {
    tone = 'warn';
    text = 'ENTRIES OFF';
    hint = 'New entries are switched off. Exits and stops keep running.';
  } else if (recoveryNeedsAttention(status)) {
    tone = 'warn';
    text = 'RECOVERY';
    hint = `Recovery: ${recoveryLabel(status.recoveryState)}. New entries are blocked until it clears.`;
  } else if (engaged === null) {
    tone = 'warn';
    text = 'LIVE ?';
    hint = 'Safe-mode state could not be read.';
  }
  const t = TONES[tone];

  return (
    <button
      type="button"
      onClick={() => openSettingsSection('mode')}
      title={hint}
      data-testid="footer-live-state"
      className="sb-tool"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, height: 22, padding: '0 8px',
        border: `1px solid ${t.border}`, borderRadius: 6, background: t.bg, color: t.fg,
        fontFamily: 'inherit', fontSize: 9.5, fontWeight: 850, letterSpacing: '.04em',
        cursor: 'pointer', whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: t.fg, flexShrink: 0 }} />
      {text}
    </button>
  );
}

export function KiteFooterStatus({ onOpenSession }: { onOpenSession: () => void }) {
  const status = useKiteStatus().data;

  const unknown = !!status?.transient;
  const connected = !!status?.connected;
  const brokerTone = connected ? k.green : unknown ? k.orange : k.red;
  const brokerText = connected ? 'KITE' : unknown ? 'KITE ?' : 'KITE OFF';

  const brokerHint = connected
    ? `Connected${status?.user_name ? ` · ${status.user_name}` : ''}${
        status?.token_expires_at_ms
          ? ` · expires ${fmtTimeIST(status.token_expires_at_ms)} IST`
          : ''}`
    : unknown
      ? 'Could not reach Kite to check the session. The stored token is untouched — nothing has expired.'
      : 'Not connected. Click to reconnect.';

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
      <LiveStateChip />
      <button
        type="button"
        onClick={onOpenSession}
        title={brokerHint}
        className="sb-tool"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          height: 22,
          padding: '0 8px',
          border: `1px solid ${connected ? 'rgba(34, 197, 94, 0.45)' : unknown ? 'rgba(245, 158, 11, 0.45)' : 'rgba(239, 68, 68, 0.45)'}`,
          borderRadius: 6,
          background: connected ? 'rgba(34, 197, 94, 0.12)' : unknown ? 'rgba(245, 158, 11, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          boxShadow: connected ? '0 0 10px rgba(34, 197, 94, 0.22)' : 'none',
          color: brokerTone,
          fontFamily: 'inherit',
          fontSize: 9.5,
          fontWeight: 850,
          letterSpacing: '.04em',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          transition: 'all 0.15s ease-in-out',
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: brokerTone,
            boxShadow: connected ? '0 0 6px #22c55e' : 'none',
            flexShrink: 0,
          }}
        />
        {brokerText}
      </button>
    </span>
  );
}

export default KiteFooterStatus;

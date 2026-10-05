import React, { useState } from 'react';
import { useKiteAccounts, useKiteStatus } from '../../hooks/useKite';
import {
  useApplyProductionConfig,
  useEmergencyHalt,
  useEmergencySquareOff,
  useEngineConfig,
  useEngineReadiness,
  usePatchEngineConfig,
} from '../../hooks/useSterlingKiteEngine';
import {
  recoveryLabel,
  recoveryNeedsAttention,
  safeModeEngaged,
  useLiveStatus,
  useResumeSafeMode,
} from '../../hooks/useLiveStatus';
import type { EngineConfigModel } from '../../types/kiteEngine';
import { fmtAgeSeconds } from '../../lib/format';
import { ModeToggle } from './ModeToggle';
import { notifyOrder } from '../../store/useKiteNotifications';

function vehicleOrderLabel(cfg?: EngineConfigModel | null): string {
  if (!cfg) return 'option BUY orders';
  if (cfg.vehicle === 'futures') return 'futures BUY orders';
  if (cfg.vehicle === 'deep_itm_options') return 'Deep ITM option BUY orders';
  const d = cfg.target_delta;
  if (d != null && d < 0.35) return 'OTM option BUY orders';
  if (d != null && d > 0.65) return 'ITM option BUY orders';
  return 'ATM option BUY orders';
}

// Central control panel for the live Kite account:
//   - account + session status
//   - live safety status (safe mode, kill switch, recovery, broker exposure)
//   - the shared AUTO arm switch and the blockers that stop it
//   - Safeguards (production preset, Square-Off All, Kill Switch & Halt)
// Arming AUTO only requests entries; risk, safety and release gates stay authoritative.

const S: Record<string, React.CSSProperties> = {
  card: { background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 9, padding: 18, marginBottom: 16, boxShadow: '0 1px 2px rgba(0,0,0,.025)' },
  title: { color: 'var(--k-ink-5)', fontSize: 10.5, letterSpacing: .75, marginBottom: 14, fontWeight: 750 },
  hint: { color: 'var(--k-ink-6)', fontSize: 11.5, lineHeight: 1.5 },
  row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' },
  modeLabel: { fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 3 },
  modeDesc: { fontSize: 11.5, color: 'var(--k-ink-4)', lineHeight: 1.45 },
  divider: { height: 1, background: 'var(--k-hairline-3)', margin: '2px 0' },
};

type ConfirmKind = null | 'enable-auto' | 'emergency-square-off' | 'emergency-halt' | 'apply-production-preset' | 'resume';

type Tone = 'good' | 'warn' | 'bad' | 'quiet';
const TONE: Record<Tone, string> = {
  good: 'var(--k-green-deep, var(--k-green))',
  warn: '#b85c00',
  bad: 'var(--k-red-brick, var(--k-red))',
  quiet: 'var(--k-ink-5)',
};

function Chip({ tone, children, title }: { tone: Tone; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 9px', borderRadius: 999,
      border: `1px solid ${TONE[tone]}`, color: TONE[tone], fontSize: 10.5, fontWeight: 700,
      letterSpacing: .3, whiteSpace: 'nowrap',
    }}>
      <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: TONE[tone] }} />
      {children}
    </span>
  );
}

export function TradingModeControls() {
  const { data } = useKiteAccounts();
  const { data: kiteStatus } = useKiteStatus();
  const { data: cfg } = useEngineConfig();
  const setCfg = usePatchEngineConfig();
  const { data: readiness, refetch: refetchReadiness } = useEngineReadiness();
  const applyProduction = useApplyProductionConfig();
  const emergencySquareOff = useEmergencySquareOff();
  const emergencyHalt = useEmergencyHalt();
  const { data: liveResult } = useLiveStatus();
  const resumeSafeMode = useResumeSafeMode();

  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [resumeReason, setResumeReason] = useState('');
  const [resumeAck, setResumeAck] = useState(false);

  const active = data?.accounts?.find((a) => a.is_active);
  const statusMatches = !!active && (!kiteStatus?.account_id || kiteStatus.account_id === active.id);
  const connected = statusMatches && kiteStatus ? !!kiteStatus.connected : !!active?.connected;
  const transient = statusMatches && kiteStatus?.transient === true;
  const auto = cfg?.auto_execute ?? false;
  const autoBusy = setCfg.isPending;

  const live = liveResult?.available ? liveResult.status : null;
  const engaged = safeModeEngaged(live);
  const killSwitchOff = live?.liveExecution === false;
  const recoveryBlocked = recoveryNeedsAttention(live);

  const sessionChip: { tone: Tone; text: string } = !active
    ? { tone: 'quiet', text: 'No account' }
    : transient
      ? { tone: 'warn', text: 'Broker unreachable' }
      : connected
        ? { tone: 'good', text: 'Session active' }
        : { tone: 'bad', text: active.has_credentials ? 'Login required' : 'No API keys' };

  const onSignals = (side: 'left' | 'right') => {
    if (!cfg) return;
    if (side === 'right') { refetchReadiness(); setConfirm('enable-auto'); return; }
    setCfg.mutate({ auto_execute: false }, {
      onSuccess: () => notifyOrder({ kind: 'info', title: 'Automatic entry changed', message: 'Automatic entry disarmed.' }),
      onError: (err) => notifyOrder({ kind: 'error', title: 'Mode change failed', message: err.message }),
    });
  };

  // Braced on purpose: an unbraced arrow ran its second statement on every render and
  // turned auto-execution ON without a click.
  const confirmEnableAuto = () => {
    if (!cfg) return;
    setCfg.mutate({ auto_execute: true }, {
      onSuccess: () => {
        setConfirm(null);
        notifyOrder({ kind: 'info', title: 'Automatic entry changed', message: 'Automatic entry armed. Safety gates still apply.' });
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Mode change failed', message: err.message }),
    });
  };

  const handleApplyProductionPreset = () => {
    applyProduction.mutate(undefined, {
      onSuccess: () => {
        setConfirm(null);
        const msg = 'Production preset applied: Fast Trail, One-Red Exit, ADX 25, Time Stop 48, 2% Daily Loss.';
        setFeedback(msg);
        notifyOrder({ kind: 'info', title: 'Preset applied', message: msg });
        setTimeout(() => setFeedback(null), 5000);
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Apply preset failed', message: err.message }),
    });
  };

  const handleEmergencySquareOff = () => {
    emergencySquareOff.mutate(undefined, {
      onSuccess: (res) => {
        setConfirm(null);
        setFeedback(res.message);
        notifyOrder({ kind: 'info', title: 'Square-off executed', message: res.message });
        setTimeout(() => setFeedback(null), 6000);
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Square-off failed', message: err.message }),
    });
  };

  const handleEmergencyHalt = () => {
    emergencyHalt.mutate(undefined, {
      onSuccess: (res) => {
        setConfirm(null);
        setFeedback(res.message);
        notifyOrder({ kind: 'info', title: 'Emergency halt triggered', message: res.message });
        setTimeout(() => setFeedback(null), 6000);
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Emergency halt failed', message: err.message }),
    });
  };

  const handleResume = () => {
    const reason = resumeReason.trim();
    if (!reason) return;
    resumeSafeMode.mutate({ reason, acknowledgeInvestigation: resumeAck }, {
      onSuccess: () => { setConfirm(null); setResumeReason(''); setResumeAck(false); },
    });
  };

  // Everything that stops new entries, in one list: the backend readiness blockers plus
  // what the live-status feed reports. Arming AUTO is refused while any exist.
  const blockers: string[] = [...(readiness?.blockers ?? [])];
  if (engaged === true) {
    const why = live?.safeMode.reason ? ` (${live.safeMode.reason})` : '';
    blockers.push(`Safe mode is ON${why}. New entries are blocked until trading is resumed.`);
  }
  if (killSwitchOff) {
    blockers.push(live?.killSwitch === false
      ? 'Live execution is off at the release floor. New entries are disabled.'
      : 'Live execution is switched off (kill switch). New entries are disabled.');
  }
  if (recoveryBlocked) {
    blockers.push(`Recovery: ${recoveryLabel(live?.recoveryState)}. Resolve it before new entries.`);
  }
  const autoBlocked = blockers.length > 0;

  const exposure = live?.exposure;
  const exposureText = exposure?.observed === false
    ? 'Never observed'
    : exposure?.fresh == null
    ? null
    : exposure.fresh
      ? `Fresh${exposure.ageS != null ? ` · ${fmtAgeSeconds(exposure.ageS)}` : ''}`
      : `Stale${exposure.ageS != null ? ` · ${fmtAgeSeconds(exposure.ageS)}` : ''}`;

  return (
    <div style={S.card}>
      <div style={{ ...S.row, marginBottom: 14 }}>
        <div style={{ ...S.title, marginBottom: 0 }}>
          TRADING CONFIG{active ? ` · ${active.label}` : ''}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Chip tone="good" title="Sterling trades the real Zerodha account">LIVE</Chip>
          <Chip tone={sessionChip.tone}>{sessionChip.text}</Chip>
        </div>
      </div>

      {feedback && (
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: '#eef2ff', border: '1px solid #c7d2fe', color: '#3730a3', fontSize: 11.5 }}>
          {feedback}
        </div>
      )}

      {!active && (
        <div style={{ ...S.hint, marginBottom: 12 }}>Add and activate a Kite account in Account &amp; Login.</div>
      )}

      {/* Safe mode: the one state that needs an explicit operator action. */}
      {engaged === true && (
        <div role="alert" style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 7, background: '#fdf2f2', border: '1px solid #f8b4b4', color: '#9b1c1c', fontSize: 11.5, lineHeight: 1.5 }}>
          <div style={{ ...S.row, flexWrap: 'nowrap' }}>
            <div style={{ minWidth: 0 }}>
              <strong>Safe mode ON.</strong> New entries are blocked; exits and protection keep running.
              {live?.safeMode.reason && <div>Reason: {live.safeMode.reason}</div>}
              {live && live.safeMode.triggers.length > 0 && <div>Triggers: {live.safeMode.triggers.join(', ')}</div>}
            </div>
            <button
              type="button"
              onClick={() => setConfirm('resume')}
              disabled={resumeSafeMode.isPending}
              style={{ flexShrink: 0, minHeight: 30, padding: '0 12px', background: 'var(--k-bg)', border: '1px solid #9b1c1c', color: '#9b1c1c', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              {resumeSafeMode.isPending ? 'Resuming…' : 'Resume trading'}
            </button>
          </div>
        </div>
      )}

      {live && (
        <div data-testid="live-status" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {engaged !== true && (
            <Chip tone={engaged === false ? 'good' : 'quiet'}>
              Safe mode {engaged === false ? 'off' : 'unknown'}
            </Chip>
          )}
          <Chip tone={live.liveExecution === false ? 'bad' : live.liveExecution ? 'good' : 'quiet'}>
            Entries {live.liveExecution === false ? 'disabled' : live.liveExecution ? 'enabled' : 'unknown'}
          </Chip>
          {live.recoveryState && (
            <Chip tone={recoveryBlocked ? 'warn' : 'quiet'}>Recovery: {recoveryLabel(live.recoveryState)}</Chip>
          )}
          {exposureText && (
            <Chip tone={exposure?.fresh ? 'good' : 'warn'}>Broker exposure {exposureText}</Chip>
          )}
        </div>
      )}

      <div style={S.row}>
        <div style={{ minWidth: 0 }}>
          <div style={S.modeLabel}>AUTOMATIC ENTRY</div>
          <div style={S.modeDesc}>
            {auto
              ? 'AUTO armed. Strategy switches may request entries; safety gates can still refuse.'
              : 'MANUAL. No automatic entries are requested.'}
          </div>
        </div>
        <ModeToggle
          left="MANUAL" right="AUTO"
          value={auto ? 'right' : 'left'}
          onSelect={onSignals}
          leftColor="var(--k-blue-kite)" rightColor="var(--k-amber-2)"
          rightDotWhenActive busy={autoBusy}
        />
      </div>

      {auto && autoBlocked && (
        <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 7, background: '#fff7f0', border: '1px solid #edd6c6', fontSize: 11.5, color: '#9a4b16', lineHeight: 1.5 }}>
          <strong>AUTO armed but BLOCKED.</strong>
          <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
            {blockers.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </div>
      )}
      {auto && !connected && (
        <div style={{ marginTop: 8, ...S.hint }}>
          AUTO is armed but the broker is not connected. Log in to allow entries.
        </div>
      )}

      {/* ── Safeguards: always available, never gated on mode ── */}
      <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 8, background: 'var(--k-surface-sunken-2)', border: '1px solid var(--k-border)', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 11, fontWeight: 750, color: 'var(--k-text)', letterSpacing: 0.4 }}>
            SAFEGUARDS
          </div>
          <button
            onClick={() => setConfirm('apply-production-preset')}
            disabled={applyProduction.isPending}
            style={{ background: 'var(--k-bg)', border: '1px solid var(--k-border-strong-2)', color: 'var(--k-brand)', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700, cursor: 'pointer' }}
            title="ADX 25, Time Stop 48, Daily Loss 2%, dual stop protection"
          >
            {applyProduction.isPending ? 'Applying…' : 'Load Production Preset'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => setConfirm('emergency-square-off')}
            disabled={emergencySquareOff.isPending}
            style={{ flex: 1, minHeight: 30, background: 'var(--k-bg)', border: '1px solid var(--k-red-brick)', color: 'var(--k-red-brick)', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
          >
            {emergencySquareOff.isPending ? 'Closing…' : 'Square-Off All'}
          </button>
          <button
            onClick={() => setConfirm('emergency-halt')}
            disabled={emergencyHalt.isPending}
            style={{ flex: 1, minHeight: 30, background: 'var(--k-red-brick)', border: '1px solid var(--k-red-brick)', color: '#fff', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
          >
            {emergencyHalt.isPending ? 'Halting…' : 'Kill Switch & Halt'}
          </button>
        </div>
      </div>

      {confirm === 'enable-auto' && (
        <ConfirmModal
          title="Arm automatic entry" accent="var(--k-amber-2)" busy={autoBusy}
          confirmLabel={autoBusy ? 'Arming…' : autoBlocked ? 'Blockers Exist' : 'Arm Auto'}
          disabled={autoBlocked}
          onCancel={() => setConfirm(null)} onConfirm={confirmEnableAuto}
          body={
            <>
              <div>Ready board tickets will place <strong>1-lot {vehicleOrderLabel(cfg)}</strong> on{' '}
              <strong>your Zerodha account</strong>, subject to risk and safety checks. Same ticket Manual would Buy.</div>
              {autoBlocked && (
                <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 6, background: '#fdf2f2', border: '1px solid #f8b4b4', color: '#9b1c1c', fontSize: 11 }}>
                  <strong>Pre-Flight Blockers:</strong>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                    {blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </div>
              )}
            </>
          }
        />
      )}

      {confirm === 'resume' && (
        <ConfirmModal
          title="Resume trading" accent="var(--k-red-brick)" busy={resumeSafeMode.isPending}
          confirmLabel={resumeSafeMode.isPending ? 'Resuming…' : 'Confirm resume'}
          disabled={resumeReason.trim().length < 3 || (live?.safeMode.requiresAck === true && !resumeAck)}
          onCancel={() => setConfirm(null)} onConfirm={handleResume}
          body={
            <>
              <div>Clears safe mode and allows new entries again. Confirm the cause is resolved.</div>
              <input
                aria-label="Reason for resuming"
                value={resumeReason}
                onChange={(e) => setResumeReason(e.target.value)}
                placeholder="Reason (required)"
                style={{ marginTop: 10, width: '100%', boxSizing: 'border-box', padding: '7px 9px', borderRadius: 6, border: '1px solid var(--k-border-strong-2)', background: 'var(--k-bg)', color: 'var(--k-text)', fontSize: 12, fontFamily: 'inherit' }}
              />
              {live?.safeMode.requiresAck && (
                <label style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 11.5 }}>
                  <input type="checkbox" checked={resumeAck} onChange={(e) => setResumeAck(e.target.checked)} style={{ marginTop: 2 }} />
                  <span>I checked positions, orders and stops, and the cause is resolved.</span>
                </label>
              )}
            </>
          }
        />
      )}

      {confirm === 'emergency-square-off' && (
        <ConfirmModal
          title="Square-Off All" accent="var(--k-red-brick)" busy={emergencySquareOff.isPending}
          confirmLabel={emergencySquareOff.isPending ? 'Closing…' : 'Confirm Square-Off'}
          onCancel={() => setConfirm(null)} onConfirm={handleEmergencySquareOff}
          body={<>Cancels all resting broker GTT stops and sends <strong>MARKET sell orders</strong> to flatten every open position on this account. Continue?</>}
        />
      )}

      {confirm === 'emergency-halt' && (
        <ConfirmModal
          title="Kill Switch & Halt" accent="var(--k-red-brick)" busy={emergencyHalt.isPending}
          confirmLabel={emergencyHalt.isPending ? 'Halting…' : 'Confirm Kill Switch'}
          onCancel={() => setConfirm(null)} onConfirm={handleEmergencyHalt}
          body={<>Engages the <strong>global Kill Switch</strong> (no new orders), turns off <strong>Auto-Execute</strong> and market-exits all open positions. Continue?</>}
        />
      )}

      {confirm === 'apply-production-preset' && (
        <ConfirmModal
          title="Apply Production Preset" accent="var(--k-brand)" busy={applyProduction.isPending}
          confirmLabel={applyProduction.isPending ? 'Applying…' : 'Apply Preset'}
          onCancel={() => setConfirm(null)} onConfirm={handleApplyProductionPreset}
          body={<>Sets SuperTrend to:<br />
          • Fast trail (1.0 mult)<br />
          • One-Red exit<br />
          • ADX ≥ 25<br />
          • 48-bar time stop<br />
          • 2.0% daily-loss limit<br />
          • Dual stop (broker GTT + monitor)</>}
        />
      )}
    </div>
  );
}

function ConfirmModal({
  title,
  accent,
  body,
  confirmLabel,
  busy,
  disabled = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  accent: string;
  body: React.ReactNode;
  confirmLabel: string;
  busy: boolean;
  disabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 3000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
    >
      <div role="dialog" aria-label={title} style={{ width: 400, background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 10, padding: '22px 24px', boxShadow: '0 16px 40px rgba(0,0,0,.16)' }}>
        <div style={{ fontSize: 15, fontWeight: 800, color: accent, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--k-text)', lineHeight: 1.6, marginBottom: 18 }}>{body}</div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={busy}
            style={{ minHeight: 36, background: 'var(--k-bg)', color: 'var(--k-ink-3)', border: '1px solid var(--k-border-strong-2)', padding: '0 14px', borderRadius: 7, cursor: busy ? 'wait' : 'pointer', fontFamily: 'inherit', fontSize: 12, fontWeight: 600 }}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={busy || disabled}
            style={{ minHeight: 36, background: disabled ? 'var(--k-dim)' : accent, color: 'var(--k-bg)', border: `1px solid ${disabled ? 'var(--k-border)' : accent}`, padding: '0 16px', borderRadius: 7, cursor: (busy || disabled) ? 'not-allowed' : 'pointer', fontFamily: 'inherit', fontSize: 12, fontWeight: 700 }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default TradingModeControls;

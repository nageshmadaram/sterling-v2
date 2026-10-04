import React, { useState } from 'react';
import { useKiteAccounts, useUpdateKiteAccount } from '../../hooks/useKite';
import {
  useApplyProductionConfig,
  useEmergencyHalt,
  useEmergencySquareOff,
  useEngineConfig,
  useEngineReadiness,
  usePatchEngineConfig,
} from '../../hooks/useSterlingKiteEngine';
import type { EngineConfigModel } from '../../types/kiteEngine';
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

// Central trading-mode panel for the active Kite account. Two orthogonal axes:
//   • BROKER MODE — paper simulation vs the real Kite account.
//   • AUTO ARMING — whether automatic entry is requested.
// Neither switch grants live-capital permission. The release-wide execution floor
// and strategy/account safety gates remain authoritative.

const S: Record<string, React.CSSProperties> = {
  card: { background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 9, padding: 18, marginBottom: 16, boxShadow: '0 1px 2px rgba(0,0,0,.025)' },
  title: { color: 'var(--k-ink-5)', fontSize: 10.5, letterSpacing: .75, marginBottom: 14, fontWeight: 750 },
  hint: { color: 'var(--k-ink-6)', fontSize: 11.5, lineHeight: 1.5 },
  row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' },
  modeLabel: { fontSize: 10, letterSpacing: .75, color: 'var(--k-ink-5)', fontWeight: 750, marginBottom: 3 },
  modeDesc: { fontSize: 11.5, color: 'var(--k-ink-4)', lineHeight: 1.45 },
  divider: { height: 1, background: 'var(--k-hairline-3)', margin: '2px 0' },
};

type ConfirmKind = null | 'go-live' | 'enable-auto' | 'emergency-square-off' | 'emergency-halt' | 'apply-production-preset';

export function TradingModeControls() {
  const { data } = useKiteAccounts();
  const update = useUpdateKiteAccount();
  const { data: cfg } = useEngineConfig();
  const setCfg = usePatchEngineConfig();
  const { data: readiness, refetch: refetchReadiness } = useEngineReadiness();
  const applyProduction = useApplyProductionConfig();
  const emergencySquareOff = useEmergencySquareOff();
  const emergencyHalt = useEmergencyHalt();

  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const active = data?.accounts.find((a) => a.is_active);
  const hasKeys = !!active?.has_credentials;
  const connected = !!active?.connected;
  const isPaper = active ? active.is_paper : true;
  const isLive = !!active && !active.is_paper;
  const auto = cfg?.auto_execute ?? false;
  const execBusy = update.isPending;
  const autoBusy = setCfg.isPending;

  if (!active) {
    return (
      <div style={S.card}>
        <div style={S.title}>TRADING CONFIG</div>
        <div style={S.hint}>Add and activate a Kite account. Then select broker mode and automatic-entry arming.</div>
      </div>
    );
  }

  const onExec = (side: 'left' | 'right') => {
    if (side === 'right') { refetchReadiness(); setConfirm('go-live'); return; }
    update.mutate({ id: active.id, is_paper: true }, {
      onSuccess: () => notifyOrder({ kind: 'info', title: 'Trading mode changed', message: 'Account switched to Paper trading.' }),
      onError: (err) => notifyOrder({ kind: 'error', title: 'Mode change failed', message: err.message }),
    });
  };

  // Braced on purpose. As a concise arrow body this ended at the first
  // semicolon, which left the second `update.mutate` as a statement in the
  // component body — so rendering this panel switched the account to LIVE with
  // no click, and each success re-rendered and did it again.
  const confirmGoLive = () => {
    update.mutate({ id: active.id, is_paper: false }, {
      onSuccess: () => {
        setConfirm(null);
        notifyOrder({ kind: 'info', title: 'Broker mode changed', message: 'Live broker account selected. Capital permission is unchanged.' });
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Mode change failed', message: err.message }),
    });
  };

  const onSignals = (side: 'left' | 'right') => {
    if (!cfg) return;
    if (side === 'right') { refetchReadiness(); setConfirm('enable-auto'); return; }
    setCfg.mutate({ auto_execute: false }, {
      onSuccess: () => notifyOrder({ kind: 'info', title: 'Automatic entry changed', message: 'Automatic entry disarmed.' }),
      onError: (err) => notifyOrder({ kind: 'error', title: 'Mode change failed', message: err.message }),
    });
  };

  // Same defect as confirmGoLive: unbraced, the second statement ran on every
  // render and turned auto-execution ON without a click.
  const confirmEnableAuto = () => {
    if (!cfg) return;
    setCfg.mutate({ auto_execute: true }, {
      onSuccess: () => {
        setConfirm(null);
        notifyOrder({ kind: 'info', title: 'Automatic entry changed', message: 'Automatic entry armed. Safety and release gates still apply.' });
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
        notifyOrder({ kind: 'info', title: 'Square off executed', message: res.message });
        setTimeout(() => setFeedback(null), 6000);
      },
      onError: (err) => notifyOrder({ kind: 'error', title: 'Square off failed', message: err.message }),
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

  const hasBlockers = (readiness?.blockers?.length ?? 0) > 0;

  return (
    <div style={S.card}>
      <div style={S.title}>TRADING CONFIG · {active.label}</div>

      {feedback && (
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: '#eef2ff', border: '1px solid #c7d2fe', color: '#3730a3', fontSize: 11.5 }}>
          {feedback}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={S.row}>
          <div style={{ minWidth: 0 }}>
            <div style={S.modeLabel}>BROKER MODE</div>
            <div style={S.modeDesc}>
              {isLive
                ? 'LIVE ACCOUNT — Sterling reads the real Zerodha account. New exposure still requires release and safety permission.'
                : 'PAPER — entry and exit orders are simulated.'}
            </div>
          </div>
          <ModeToggle
            left="PAPER" right="LIVE"
            value={isPaper ? 'left' : 'right'}
            onSelect={onExec}
            leftColor="var(--k-blue-kite)" rightColor="var(--k-green)"
            rightDotWhenActive busy={execBusy}
            rightDisabled={!hasKeys}
            rightTitle={hasKeys ? undefined : 'Add API keys first to select the live broker account.'}
          />
        </div>

        <div style={S.divider} />

        <div style={S.row}>
          <div style={{ minWidth: 0 }}>
            <div style={S.modeLabel}>AUTOMATIC ENTRY</div>
            <div style={S.modeDesc}>
              {auto
                ? 'AUTO ARMED — strategy-specific switches may request entries. Release and safety gates can still refuse.'
                : 'AUTO OFF — no automatic entry is requested by the shared master switch.'}
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
      </div>

      {isLive && auto && (
        <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 7, background: '#fff7f0', border: '1px solid #edd6c6', fontSize: 11.5, color: '#9a4b16', lineHeight: 1.5 }}>
          <strong>LIVE ACCOUNT + AUTO ARMED.</strong> This requests automatic entry. It does not grant capital permission. Release, lane, account, and safety gates still decide whether any broker write is allowed.
        </div>
      )}
      {auto && !connected && (
        <div style={{ marginTop: 8, ...S.hint }}>
          AUTO is armed, but the broker account is not connected. Log in before Sterling can validate any entry request.
        </div>
      )}

      {/* ── Live Safeguards & Emergency Control Surface ── */}
      {isLive && (
        <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 8, background: 'var(--k-surface-sunken-2)', border: '1px solid var(--k-border)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontSize: 11, fontWeight: 750, color: 'var(--k-text)', letterSpacing: 0.4 }}>
              🛡️ SAFEGUARDS & CONTROLS
            </div>
            <button
              onClick={() => setConfirm('apply-production-preset')}
              disabled={applyProduction.isPending}
              style={{ background: 'var(--k-bg)', border: '1px solid var(--k-border-strong-2)', color: 'var(--k-brand)', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700, cursor: 'pointer' }}
              title="Apply 7.5y sweep-validated parameters (ADX 25, Time Stop 48, Daily Loss 2%)"
            >
              {applyProduction.isPending ? 'Applying…' : '⚡ Load Production Preset'}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => setConfirm('emergency-square-off')}
              disabled={emergencySquareOff.isPending}
              style={{ flex: 1, minHeight: 30, background: 'var(--k-bg)', border: '1px solid var(--k-red-brick)', color: 'var(--k-red-brick)', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              {emergencySquareOff.isPending ? 'Closing…' : '🚨 Square-Off All'}
            </button>
            <button
              onClick={() => setConfirm('emergency-halt')}
              disabled={emergencyHalt.isPending}
              style={{ flex: 1, minHeight: 30, background: 'var(--k-red-brick)', border: '1px solid var(--k-red-brick)', color: '#fff', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              {emergencyHalt.isPending ? 'Halting…' : '🛑 Kill Switch & Halt'}
            </button>
          </div>
        </div>
      )}

      {confirm === 'go-live' && (
        <ConfirmModal
          title="Use LIVE broker account" accent="var(--k-green)" busy={execBusy}
          confirmLabel={execBusy ? 'Switching…' : hasBlockers ? 'Blockers Exist' : 'Use LIVE account'}
          disabled={hasBlockers}
          onCancel={() => setConfirm(null)} onConfirm={confirmGoLive}
          body={
            <>
              <div>Sterling will use <strong>{active.label}</strong> as the real broker account for positions, margin, orders, and reconciliation. This selection does not enable live-capital entry. The release-wide execution floor and all safety gates remain authoritative{auto ? ', and AUTO is currently armed' : ''}.</div>
              {hasBlockers && (
                <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 6, background: '#fdf2f2', border: '1px solid #f8b4b4', color: '#9b1c1c', fontSize: 11 }}>
                  <strong>Pre-Flight Blockers:</strong>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                    {readiness?.blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </div>
              )}
            </>
          }
        />
      )}

      {confirm === 'enable-auto' && (
        <ConfirmModal
          title="Arm automatic entry" accent="var(--k-amber-2)" busy={autoBusy}
          confirmLabel={autoBusy ? 'Arming…' : hasBlockers ? 'Blockers Exist' : 'Arm Auto'}
          disabled={hasBlockers}
          onCancel={() => setConfirm(null)} onConfirm={confirmEnableAuto}
          body={
            <>
              <div>Ready board tickets will place <strong>1-lot {vehicleOrderLabel(cfg)}</strong> on{' '}
              {isLive ? <strong>your real Zerodha account</strong> : 'the paper account'} under the live-safety gate. Same ticket Manual would Buy.</div>
              {hasBlockers && (
                <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 6, background: '#fdf2f2', border: '1px solid #f8b4b4', color: '#9b1c1c', fontSize: 11 }}>
                  <strong>Pre-Flight Blockers:</strong>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                    {readiness?.blockers.map((b, i) => <li key={i}>{b}</li>)}
                  </ul>
                </div>
              )}
            </>
          }
        />
      )}

      {confirm === 'emergency-square-off' && (
        <ConfirmModal
          title="🚨 Emergency Square-Off All" accent="var(--k-red-brick)" busy={emergencySquareOff.isPending}
          confirmLabel={emergencySquareOff.isPending ? 'Closing…' : 'Confirm Square-Off'}
          onCancel={() => setConfirm(null)} onConfirm={handleEmergencySquareOff}
          body={<>This will immediately <strong>cancel all resting broker GTT stops</strong> and submit <strong>MARKET sell orders</strong> to flatten all open positions for this account. Continue?</>}
        />
      )}

      {confirm === 'emergency-halt' && (
        <ConfirmModal
          title="🛑 Emergency Kill Switch & Halt" accent="var(--k-red-brick)" busy={emergencyHalt.isPending}
          confirmLabel={emergencyHalt.isPending ? 'Halting…' : 'Confirm Kill Switch'}
          onCancel={() => setConfirm(null)} onConfirm={handleEmergencyHalt}
          body={<>This will engage the <strong>global Kill Switch</strong> (halting all new orders), turn off <strong>Auto-Execute</strong>, and market-exit all open positions. Continue?</>}
        />
      )}

      {confirm === 'apply-production-preset' && (
        <ConfirmModal
          title="⚡ Apply Production Preset" accent="var(--k-brand)" busy={applyProduction.isPending}
          confirmLabel={applyProduction.isPending ? 'Applying…' : 'Apply Preset'}
          onCancel={() => setConfirm(null)} onConfirm={handleApplyProductionPreset}
          body={<>Load verified hyperparameters from the 7.5y IS/OOS sweep:<br />
          • Fast SuperTrend trail (1.0 mult)<br />
          • One-Red tightest auto-exit<br />
          • ADX ≥ 25 filter (filters chop)<br />
          • 48-bar time-stop (curbs option theta bleed)<br />
          • 2.0% daily-loss circuit breaker<br />
          • Dual stop protection (broker GTT + WS monitor)</>}
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
      <div style={{ width: 400, background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 10, padding: '22px 24px', boxShadow: '0 16px 40px rgba(0,0,0,.16)' }}>
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


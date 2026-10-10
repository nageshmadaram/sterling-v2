import { useState } from 'react';
import {
  SIM_STRATEGIES, STRATEGY_MODES, describeSimError, isOwnerDenied, strategyLabel, useSetStrategyMode, useSimStatus,
  type SimErrorView, type StrategyKey, type StrategyMode,
} from '../../hooks/useSim';
import { useIsSimulation } from '../../store/useViewStore';
import { notifyOrder } from '../../store/useKiteNotifications';
import { SimDialog } from './SimDialog';
import { SIM, TAP } from './simStyle';

/**
 * MANUAL | SIMULATION | AUTO for each of the three strategies.
 *
 *  - MANUAL     nothing trades by itself
 *  - SIMULATION it trades by itself with virtual money, no real orders
 *  - AUTO       it trades by itself with REAL money on the Zerodha account
 *
 * Both of the last two ask first and say whose money it is. In the Simulation view this is a
 * read-only copy: real-money AUTO is armed from the LIVE view only (the server refuses it here).
 */
export const MODE_TEXT: Record<StrategyMode, string> = {
  MANUAL: 'Off. Nothing is traded automatically.',
  SIMULATION: 'Trades by itself with virtual money. No real orders.',
  AUTO: 'Trades by itself with REAL money on your Zerodha account.',
};

const MODE_ACCENT: Record<StrategyMode, string> = {
  MANUAL: SIM.info,
  SIMULATION: SIM.accent,
  AUTO: SIM.danger,
};

interface Pending { strategy: StrategyKey; mode: 'SIMULATION' | 'AUTO' }

export function StrategyModeSelector({ readOnly }: { readOnly?: boolean }) {
  const inSimulation = useIsSimulation();
  const locked = readOnly ?? inSimulation;
  const q = useSimStatus();
  const setMode = useSetStrategyMode();
  const [pending, setPending] = useState<Pending | null>(null);
  const [errors, setErrors] = useState<Partial<Record<StrategyKey, SimErrorView>>>({});
  const [busyKey, setBusyKey] = useState<StrategyKey | null>(null);

  // Not the owner: nothing here is theirs to change, so it stays out of sight.
  if (q.isError && isOwnerDenied(q.error)) return null;

  const apply = (strategy: StrategyKey, mode: StrategyMode) => {
    setErrors((e) => ({ ...e, [strategy]: undefined }));
    setBusyKey(strategy);
    setMode.mutate({ strategy, mode }, {
      onSuccess: () => {
        setPending(null);
        notifyOrder({
          kind: 'info', title: 'Strategy mode changed',
          message: `${strategyLabel(strategy)} is now ${mode}${mode === 'SIMULATION' ? ' (virtual money)' : mode === 'AUTO' ? ' (real money)' : ''}.`,
        });
      },
      onError: (err) => {
        setPending(null);
        setErrors((e) => ({ ...e, [strategy]: describeSimError(err) }));
      },
      onSettled: () => setBusyKey(null),
    });
  };

  const choose = (strategy: StrategyKey, current: StrategyMode, next: StrategyMode) => {
    if (next === current) return;
    if (next === 'MANUAL') { apply(strategy, next); return; }   // switching OFF needs no warning
    setPending({ strategy, mode: next });
  };

  return (
    <div data-testid="strategy-modes" style={{ display: 'flex', flexDirection: 'column' }}>
      {locked && (
        <div style={{ fontSize: 11.5, color: SIM.muted, lineHeight: 1.5, marginBottom: 6 }}>
          Shown for reference. Change a strategy&apos;s mode from the LIVE view.
        </div>
      )}
      {SIM_STRATEGIES.map(({ key, label, blurb }) => {
        const state = q.data?.strategies?.[key];
        const mode: StrategyMode = state?.mode ?? 'MANUAL';
        const known = !!state;
        const rowBusy = busyKey === key && setMode.isPending;
        const err = errors[key];
        return (
          <div key={key} data-testid={`mode-row-${key}`} style={{ padding: '12px 0', borderTop: `1px solid ${SIM.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0, flex: '1 1 200px' }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: SIM.text }}>{label}</div>
                <div style={{ fontSize: 11.5, color: SIM.muted, lineHeight: 1.45 }}>{blurb}</div>
              </div>
              {locked ? (
                <span
                  data-testid={`mode-badge-${key}`}
                  style={{
                    padding: '5px 12px', borderRadius: 999, border: `1px solid ${MODE_ACCENT[mode]}`, color: MODE_ACCENT[mode],
                    fontSize: 11, fontWeight: 800, letterSpacing: 0.6,
                  }}
                >
                  {known ? mode : '…'}
                </span>
              ) : (
                <div role="group" aria-label={`${label} mode`} style={{
                  display: 'inline-flex', gap: 2, padding: 3, background: SIM.sunken, border: `1px solid ${SIM.border}`, borderRadius: 8, maxWidth: '100%',
                }}>
                  {STRATEGY_MODES.map((m) => {
                    const on = known && mode === m;
                    return (
                      <button
                        key={m}
                        type="button"
                        aria-pressed={on}
                        disabled={!known || rowBusy}
                        onClick={() => choose(key, mode, m)}
                        style={{
                          minHeight: TAP - 4, padding: '0 11px', border: 'none', borderRadius: 6,
                          background: on ? SIM.surface : 'transparent', color: on ? SIM.text : SIM.faint,
                          fontFamily: 'inherit', fontSize: 10.5, fontWeight: on ? 800 : 600, letterSpacing: '0.06em', whiteSpace: 'nowrap',
                          boxShadow: on ? `inset 0 -2px ${MODE_ACCENT[m]}, 0 1px 2px rgba(0,0,0,0.12)` : 'none',
                          cursor: !known || rowBusy ? 'wait' : on ? 'default' : 'pointer',
                          opacity: !known ? 0.5 : 1,
                        }}
                      >
                        {m}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: known && mode === 'AUTO' ? SIM.danger : SIM.muted, marginTop: 6, lineHeight: 1.45 }}>
              {rowBusy ? 'Switching…' : known ? MODE_TEXT[mode] : q.isError ? 'Mode unavailable right now.' : 'Loading…'}
            </div>
            {state?.conflict && (
              <div role="alert" style={{ fontSize: 11.5, color: SIM.danger, marginTop: 4, lineHeight: 1.45 }}>
                Both real and virtual trading are switched on for this strategy. Choose MANUAL first, then the mode you want.
              </div>
            )}
            {err && (
              <div role="alert" data-testid={`mode-error-${key}`} style={{
                marginTop: 8, padding: '8px 10px', borderRadius: 7, background: 'var(--k-tint-red)', border: `1px solid ${SIM.danger}`,
                color: SIM.text, fontSize: 12, lineHeight: 1.5,
              }}>
                {err.text}
                {err.reasons.length > 0 && (
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                    {err.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        );
      })}

      {pending?.mode === 'SIMULATION' && (
        <SimDialog
          title={`${strategyLabel(pending.strategy)}: switch to Simulation`}
          accent={SIM.accent}
          busy={setMode.isPending}
          confirmLabel={setMode.isPending ? 'Switching…' : 'Start Simulation'}
          onCancel={() => setPending(null)}
          onConfirm={() => apply(pending.strategy, 'SIMULATION')}
        >
          {strategyLabel(pending.strategy)} will start trading by itself with <strong>virtual money</strong>. No real
          orders are sent and your Zerodha account is not touched. If it was on AUTO, real automatic trading for it stops.
        </SimDialog>
      )}
      {pending?.mode === 'AUTO' && (
        <SimDialog
          title={`${strategyLabel(pending.strategy)}: switch to AUTO (real money)`}
          accent={SIM.danger}
          busy={setMode.isPending}
          confirmLabel={setMode.isPending ? 'Arming…' : 'Arm AUTO with real money'}
          onCancel={() => setPending(null)}
          onConfirm={() => apply(pending.strategy, 'AUTO')}
        >
          {strategyLabel(pending.strategy)} will place <strong>REAL orders on your Zerodha account</strong> by itself,
          using <strong>real money</strong>. Any losses are real. Simulation for this strategy is switched off.
          Safety checks still apply and can refuse an order.
        </SimDialog>
      )}
    </div>
  );
}

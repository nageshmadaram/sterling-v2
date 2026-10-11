import type { CSSProperties } from 'react';
import { useView, useViewStore, type AppView } from '../../store/useViewStore';
import { isOwnerDenied, useSimStatus } from '../../hooks/useSim';
import { hasUnsavedDraft } from '../kite/config/unsavedDraftGuard';
import { SIM } from './simStyle';

/**
 * The header's LIVE | SIMULATION switch.
 *
 * Switching rebuilds the screen on the other book (see ViewQueryProvider), so an unapplied
 * settings draft would be lost; the same question the navigation asks is asked here first.
 * The switch stays out of sight for anyone who is not the owner (the status route says 403),
 * but never while the Simulation view is open: it is the way back.
 */
const OPTIONS: ReadonlyArray<{ view: AppView; label: string; accent: string; hint: string }> = [
  { view: 'live', label: 'LIVE', accent: SIM.live, hint: 'Your real Zerodha account' },
  { view: 'simulation', label: 'SIMULATION', accent: SIM.accent, hint: 'Practice with virtual money. No real orders.' },
];

const seg = (active: boolean, accent: string): CSSProperties => ({
  minHeight: 30,
  padding: '0 11px',
  border: 'none',
  borderRadius: 6,
  background: active ? SIM.surface : 'transparent',
  color: active ? SIM.text : SIM.faint,
  fontFamily: 'inherit',
  fontSize: 10.5,
  fontWeight: active ? 800 : 600,
  letterSpacing: '0.07em',
  lineHeight: 1,
  whiteSpace: 'nowrap',
  cursor: active ? 'default' : 'pointer',
  boxShadow: active ? `inset 0 -2px ${accent}, 0 1px 2px rgba(0,0,0,0.12)` : 'none',
});

export function ViewSwitch() {
  const view = useView();
  const setView = useViewStore((s) => s.setView);
  const status = useSimStatus();

  const visible = view === 'simulation'
    || status.isSuccess
    || (status.isError && !isOwnerDenied(status.error));
  if (!visible) return null;

  const choose = (next: AppView) => {
    if (next === view) return;
    if (hasUnsavedDraft() && !window.confirm('You have unsaved settings changes. Switch view and discard them?')) return;
    setView(next);
  };

  return (<>
    <button className="mobile-view-toggle" type="button" role="switch" aria-label="Live trading view" aria-checked={view==='live'} title={view==='live'?'Switch to simulation · virtual money':'Switch to live · real account'} onClick={()=>choose(view==='live'?'simulation':'live')}>{view==='live'?'Live':'Simulation'}<span aria-hidden="true">⇄</span></button>
    <div className="desktop-view-switch"
      role="group"
      aria-label="Trading view"
      data-testid="view-switch"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 2, padding: 3, flexShrink: 0,
        background: SIM.sunken, border: `1px solid ${SIM.border}`, borderRadius: 8,
      }}
    >
      {OPTIONS.map((o) => (
        <button
          key={o.view}
          type="button"
          aria-pressed={view === o.view}
          title={o.hint}
          onClick={() => choose(o.view)}
          style={seg(view === o.view, o.accent)}
        >
          {o.label}
        </button>
      ))}
    </div></>
  );
}

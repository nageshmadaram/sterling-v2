import { useView, useViewStore, type AppView } from '../../store/useViewStore';
import { isOwnerDenied, useSimStatus } from '../../hooks/useSim';
import { hasUnsavedDraft } from '../kite/config/unsavedDraftGuard';

/**
 * The header's LIVE | SIMULATION switch.
 *
 * Switching rebuilds the screen on the other book (see ViewQueryProvider), so an unapplied
 * settings draft would be lost; the same question the navigation asks is asked here first.
 * The switch stays out of sight for anyone who is not the owner (the status route says 403),
 * but never while the Simulation view is open: it is the way back.
 */
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

  return <button data-testid="view-switch" className="mobile-view-toggle unified-view-toggle" type="button" role="switch" aria-label="Live trading view" aria-checked={view==='live'} title={view==='live'?'Switch to simulation · virtual money':'Switch to live · real account'} onClick={()=>choose(view==='live'?'simulation':'live')}>{view==='live'?'KITE':'KITE SIMULATION'}</button>;
}

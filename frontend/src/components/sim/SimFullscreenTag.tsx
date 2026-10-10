import { useIsSimulation } from '../../store/useViewStore';
import { SIM } from './simStyle';

/**
 * A full-screen pane covers the whole app, banner included. This keeps the one thing that must stay
 * true on screen: a small amber tab at the top edge saying the money is virtual. Nothing in the
 * live view; it ignores the pointer so it never sits in the way of a pane's own controls.
 */
export function SimFullscreenTag() {
  if (!useIsSimulation()) return null;
  return (
    <div
      role="status"
      data-testid="sim-fullscreen-tag"
      style={{
        position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', zIndex: 5,
        padding: '2px 14px 3px', borderRadius: '0 0 8px 8px', background: SIM.accent, color: 'var(--k-on-accent)',
        fontSize: 10.5, fontWeight: 800, letterSpacing: 0.6, whiteSpace: 'nowrap', pointerEvents: 'none',
      }}
    >
      SIMULATION — virtual money, no real orders
    </div>
  );
}

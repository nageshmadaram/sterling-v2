import { useViewStore } from '../../store/useViewStore';
import { SIM, button } from './simStyle';

/**
 * Stands where a control used to be when the Simulation view cannot offer it.
 *
 * The server refuses a handful of routes in the Simulation view because they act on the real
 * account or on the whole app (kill switch, safe-mode resume, broker login and accounts, the live
 * daily-loss limit). Showing the button and letting it fail with a 403 reads as a bug; this says
 * what is missing and why, in one line, and offers the way to where it works.
 */
export function SimDeniedNote({ what, why = 'It acts on the real Zerodha account, so it works in the LIVE view only.' }: {
  what: string;
  why?: string;
}) {
  const setView = useViewStore((s) => s.setView);
  return (
    <div
      role="note"
      data-testid="sim-denied-note"
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '9px 12px', borderRadius: 8,
        background: SIM.tint, border: `1px dashed ${SIM.accent}`, color: SIM.text, fontSize: 12, lineHeight: 1.5,
      }}
    >
      <span style={{ flex: '1 1 220px', minWidth: 0 }}>
        <strong>{what}</strong> is not available in the Simulation view. {why}
      </span>
      <button type="button" onClick={() => setView('live')} style={{ ...button(SIM.live), minHeight: 30 }}>
        Go to LIVE
      </button>
    </div>
  );
}

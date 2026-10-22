/**
 * A red bar on the signal board while Kite is not logged in. Today the session lapsed from
 * 09:08 to 11:42 and nothing said so: no prices, no orders, no trading in the opening hours.
 */
import { useKiteStatus } from '../../../hooks/useKite';
import { k, tint } from '../../../styles/kiteUI';

export function KiteSessionBar() {
  const status = useKiteStatus();
  const s = status.data;
  // `transient` means Sterling could not ask Kite this time; that is not a logged-out session.
  if (!s || s.connected || (s as { transient?: boolean }).transient) return null;
  return (
    <div role="alert" style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', flexShrink: 0,
      background: tint(k.red, 12), color: k.red, fontSize: 12, fontWeight: 600, borderBottom: `1px solid ${tint(k.red, 30)}`,
    }}>
      <span style={{ flex: 1, minWidth: 0 }}>Kite is not logged in. Prices are not live and no order can be placed. Log in every morning before 09:15 (the session ends at 06:00).</span>
      <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: 'connect' }))}
        style={{ flexShrink: 0, border: `1px solid ${k.red}`, background: 'transparent', color: k.red, borderRadius: 4, padding: '3px 10px', fontSize: 11.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
        Log in to Kite
      </button>
    </div>
  );
}

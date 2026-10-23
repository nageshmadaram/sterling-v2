/** Session truth across the live trading workspace, independent of dock layout. */
import { useKiteStatus } from '../../../hooks/useKite';
import { k, tint } from '../../../styles/kiteUI';

export function KiteSessionBar() {
  const status = useKiteStatus();
  const s = status.data;
  const unknown = status.isError || !!(s as { transient?: boolean } | undefined)?.transient;
  if (!unknown && (!s || s.connected)) return null;
  const color = unknown ? k.amber : k.red;
  return (
    <div role="alert" style={{
      display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, padding: '8px 12px', flexShrink: 0,
      background: tint(color, 12), color: k.text, fontSize: 12, fontWeight: 600, borderBottom: `1px solid ${tint(color, 30)}`,
    }}>
      <span style={{ flex: '1 1 230px', minWidth: 0 }}>{unknown
        ? 'Kite session status could not be verified. Live prices and order readiness are unconfirmed. Check the connection before a new entry.'
        : 'Kite is not logged in. Live prices and new broker entries are unavailable. Renew the session before trading; do not assume broker-held positions are closed.'}</span>
      <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: 'connect' }))}
        style={{ flexShrink: 0, border: `1px solid ${color}`, background: 'transparent', color: k.text, borderRadius: 4, padding: '6px 10px', fontSize: 11.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
        {unknown ? 'Check Kite connection' : 'Log in to Kite'}
      </button>
    </div>
  );
}

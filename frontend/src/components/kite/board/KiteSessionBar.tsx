import { useKiteAccounts, useKiteStatus } from '../../../hooks/useKite';
import { useKiteStatusCardStore } from '../../../store/useKiteStatusCardStore';

/** Compact, truthful mobile banner. Saved credentials never imply broker authority. */
export function KiteSessionBar() {
  const status = useKiteStatus();
  const accounts = useKiteAccounts();
  const s = status.data;
  if (!status.isError && (!s || s.connected)) return null;

  const current = accounts.data?.accounts.find(a =>
    a.id === (s?.account_id || accounts.data?.active_id));
  const expired = !!current?.token_expires_at_ms
    && current.token_expires_at_ms <= Date.now();
  const rejected = /\b(rejected|expired|not logged in|revoked|identity could not)\b/i
    .test(s?.message || '');
  const savedButUnverified = !!current?.connected && !expired && !rejected;
  const unknown = status.isError || !!s?.transient;
  const label = savedButUnverified ? '● Kite session saved · unverified'
    : unknown ? '● Kite check unavailable' : '● Kite disconnected';
  return <button type="button" className="kite-connection-status"
    onClick={() => useKiteStatusCardStore.getState().openCard()}
    title="Open broker connection status. A saved token is not permission to place orders.">
    {label} <span>{savedButUnverified ? 'Verify ›' : 'Connect ›'}</span>
  </button>;
}

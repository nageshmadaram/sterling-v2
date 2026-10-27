import { useKiteStatus } from '../../../hooks/useKite';
import { useKiteStatusCardStore } from '../../../store/useKiteStatusCardStore';
export function KiteSessionBar(){
  const status=useKiteStatus(),s=status.data;
  if(!status.isError && (!s||s.connected))return null;
  const unknown=status.isError||!!s?.transient;
  return <button className="kite-connection-status" onClick={()=>useKiteStatusCardStore.getState().openCard()} title="Open broker connection status. Broker-held positions remain at the broker.">{unknown?'● Connection unverified':'● Kite disconnected'} <span>Connect ›</span></button>;
}

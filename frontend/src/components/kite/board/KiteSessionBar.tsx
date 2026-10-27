import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {useKiteStatus} from '../../../hooks/useKite';
import {useKiteStatusCardStore} from '../../../store/useKiteStatusCardStore';
/** Compact session truth; the full explanation is available on explicit opening. */
export function KiteSessionBar(){
  const query=useKiteStatus(),status=query.data;
  const [host,setHost]=useState<HTMLElement|null>(null);
  useEffect(()=>setHost(document.getElementById('kite-session-status')),[]);
  const unknown=query.isError||!!status?.transient||!status;
  const label=unknown?'Unverified':status.connected?'Connected':'Disconnected';
  return host?createPortal(<button type="button" className="kite-session-indicator" data-connected={!unknown&&status?.connected} aria-label={`Kite ${label.toLowerCase()}. Open connection`} onClick={()=>useKiteStatusCardStore.getState().openCard()}><span aria-hidden="true">●</span><span className="kite-session-label">{label}</span></button>,host):null;
}

import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../../utils/api';
import { startPreferenceSync, stopPreferenceSync, flushPreferences, usePreferenceSync } from '../../appearance/preferences';
export function PreferenceGate({children}:{children:ReactNode}) {
 const [ready,setReady]=useState(false),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const status=usePreferenceSync();
 useEffect(()=>{let alive=true;let retry:ReturnType<typeof setTimeout>|undefined;api.get<{user_id:string}>('/api/v1/auth/me').then(user=>startPreferenceSync(user.user_id)).then(()=>{if(alive)setReady(true);}).catch(()=>{if(alive){setError('Could not restore your saved settings. Retrying the connection…');retry=setTimeout(()=>setAttempt(a=>a+1),3000);}});return()=>{alive=false;clearTimeout(retry);stopPreferenceSync();};},[attempt]);
 if(!ready)return <div className="sterling-session-validation" role="status"><div className="session-validation-card">{error||'Restoring your saved settings…'}{error&&<button onClick={()=>{setError('');setAttempt(a=>a+1);}}>Retry</button>}</div></div>;
 return <>{children}{status.state==='error'&&<div role="alert" style={{position:'fixed',bottom:42,right:16,zIndex:5000,maxWidth:360,padding:12,background:'var(--k-bg)',color:'var(--k-warn)',border:'1px solid var(--k-border)'}}>{status.error}<button onClick={()=>void flushPreferences()}>Retry save</button></div>}</>;
}

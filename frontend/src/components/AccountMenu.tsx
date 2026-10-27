import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useSterlingProfile } from './kite/ProfileMenu';
import { useTheme, useSetTheme } from '../store/useStore';
import { useAppearance } from '../appearance/store';
import { api } from '../utils/api';
import { clearAuthTokens } from '../utils/auth';
export function AccountMenu({children}:{children?:ReactNode}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const profile=useSterlingProfile();
  const theme=useTheme(),setTheme=useSetTheme(),appearance=useAppearance();
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const close=()=>dialog.current?.close();
  useEffect(()=>{window.addEventListener('sterling-home',close);return()=>window.removeEventListener('sterling-home',close);},[]);
  const action=(name:string)=>{close();requestAnimationFrame(()=>window.dispatchEvent(new Event(name)));};
  async function logout(){setBusy(true);try{await api.post('/api/v1/auth/logout',{});clearAuthTokens();window.location.reload();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <><button className="account-menu-trigger" aria-label="Menu" aria-haspopup="dialog" onClick={()=>dialog.current?.showModal()}>☰</button>
    <dialog ref={dialog} className="account-menu" aria-label="Account and navigation" onClick={e=>{if(e.target===e.currentTarget)close();}}>
      <div className="account-menu-content"><header><button className="account-identity" onClick={()=>action('sterling-open-profile')}>
        {profile.data?.photo?<img alt="" src={profile.data.photo}/>:<span className="account-avatar">{(profile.data?.display_name||profile.data?.username||'S').slice(0,2).toUpperCase()}</span>}
        <strong>{profile.data?.display_name||profile.data?.username||'Account'}</strong></button><button aria-label="Close menu" onClick={close}>×</button></header>
      <button onClick={()=>action('sterling-open-profile')}>Profile & security</button>
      <button onClick={()=>action('sterling-select-workspace')}>Switch workspace</button>
      <div className="account-theme" role="group" aria-label="Appearance">{(['light','dark'] as const).map(value=><button key={value} aria-pressed={theme===value} onClick={()=>{appearance.update({preset:'',base:value,accent:''});setTheme(value);}}>{value==='light'?'☀ Light':'☾ Dark'}</button>)}</div>
      {children && <div onClick={e=>{if((e.target as HTMLElement).closest('button'))close();}}>{children}</div>}
      <button disabled={busy} onClick={()=>void logout()}>Sign out</button>{error&&<p role="alert">{error}</p>}
      </div>
    </dialog></>;
}

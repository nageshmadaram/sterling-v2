import {useMobileBackLayer} from '../../hooks/useMobileBackLayer';
import { createPortal } from 'react-dom';
import { disconnectPushDevice } from './PushSettings';
import { useState, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { setAuthTokens, clearAuthTokens, type SterlingTokens } from '../../utils/auth';
import type { List, User } from './SterlingUsersPanel';
type Profile={username:string;display_name:string;photo:string;role:string;user_id:string};
export function useSterlingProfile(){return useQuery({queryKey:['sterling-profile'],queryFn:()=>api.get<Profile>('/api/v1/auth/profile'),staleTime:30000});}
export function ProfileMenu(){const q=useSterlingProfile();const qc=useQueryClient();const [open,setOpen]=useState(false),[name,setName]=useState(''),[photo,setPhoto]=useState(''),[username,setUsername]=useState(''),[current,setCurrent]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);const trigger=useRef<HTMLButtonElement>(null);const initialized=useRef(false);
 const photoInput=useRef<HTMLInputElement>(null);const photoEpoch=useRef(0);const [photoProcessing,setPhotoProcessing]=useState(false);
 useEffect(()=>{if(!open){initialized.current=false;photoEpoch.current+=1;setPhotoProcessing(false);return;}if(open&&q.data&&!initialized.current){initialized.current=true;setName(q.data.display_name);setPhoto(q.data.photo);setUsername(q.data.username);setCurrent('');setPassword('');setConfirm('');setMessage('');}},[open,q.data]);
 useEffect(()=>{const show=()=>setOpen(true);window.addEventListener('sterling-open-profile',show);return()=>window.removeEventListener('sterling-open-profile',show);},[]);
 const close=()=>{setOpen(false);setCurrent('');setPassword('');setConfirm('');if(trigger.current?.getBoundingClientRect().width)trigger.current.focus();else document.querySelector<HTMLElement>('.mobile-experience-bar [aria-label="Menu"]')?.focus();};
 const run=async(action:()=>Promise<void>)=>{setBusy(true);setMessage('');try{await action();}catch(e){setMessage(e instanceof Error?e.message:'Profile update failed');}finally{setBusy(false);}};
 const upload=async(file?:File)=>{
  if(!file)return;
  const epoch=++photoEpoch.current;
  setMessage('');
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size===0||file.size>10000000){setMessage('Choose a PNG, JPEG or WebP up to 10 MB.');return;}
  setPhotoProcessing(true);
  try{
   const image=await createImageBitmap(file);
   try{
    if(!image.width||!image.height)throw Error('Invalid dimensions');
    const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
    const ctx=canvas.getContext('2d');if(!ctx)throw Error('Canvas unavailable');
    ctx.fillStyle='#fff';ctx.fillRect(0,0,256,256);
    const side=Math.min(image.width,image.height);
    ctx.drawImage(image,(image.width-side)/2,(image.height-side)/2,side,side,0,0,256,256);
    const encoded=canvas.toDataURL('image/jpeg',.85);
    if(!encoded.startsWith('data:image/jpeg;base64,')||encoded.length>280000)throw Error('Photo too large');
    if(epoch===photoEpoch.current){setPhoto(encoded);setMessage('Picture ready. Save profile to apply.');}
   }finally{image.close();}
  }catch{if(epoch===photoEpoch.current)setMessage('Could not process that picture. Choose another image.');}
  finally{if(epoch===photoEpoch.current)setPhotoProcessing(false);}
 };
 const removePhoto=()=>{photoEpoch.current+=1;setPhotoProcessing(false);setPhoto('');setMessage('Picture removed from preview. Save profile to apply.');};
 const label=q.data?.display_name||q.data?.username||'Sterling account';
 useMobileBackLayer(open,()=>close(),'profile');
 return <><button ref={trigger} className="sterling-profile" aria-label="Sterling account profile" aria-expanded={open} onClick={()=>setOpen(!open)} style={{display:'flex',alignItems:'center',gap:6,border:0,background:'transparent',color:'var(--k-text)',cursor:'pointer'}}>{q.data?.photo?<img src={q.data.photo} alt="" width={28} height={28} style={{borderRadius:'50%',objectFit:'cover'}}/>:<span style={{borderRadius:'50%',padding:7,background:'var(--k-surface-2)'}}>{label.slice(0,2).toUpperCase()}</span>}<span>{label}</span></button>
 {open&&createPortal(<div className="appearance-modal sterling-profile-modal" role="dialog" aria-modal="true" aria-label="Sterling profile" onKeyDown={e=>{if(e.key==='Escape')close();if(e.key==='Tab'){const controls=e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not([type="file"]),select');const first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}}}><div className="profile-panel" style={{background:'var(--k-bg)',color:'var(--k-text)'}}><button autoFocus onClick={close} aria-label="Close profile">×</button><h2>Your Sterling account</h2><p>{q.data?.username} · {q.data?.role}. Broker login is managed separately in Connect.</p>{q.isError&&<p role="alert">Profile unavailable. <button type="button" className="sterling-retry-button" onClick={()=>void q.refetch()}>Retry</button></p>}
 <div className="sterling-profile-photo-editor" style={{display:'flex',alignItems:'center',gap:14,flexWrap:'wrap',padding:'12px 0'}}>
  {photo?<img src={photo} alt="Profile picture preview" width={72} height={72} style={{width:72,height:72,borderRadius:'50%',objectFit:'cover'}}/>:
   <span role="img" aria-label="No profile picture" style={{width:72,height:72,borderRadius:'50%',background:'var(--k-surface-2)',display:'grid',placeItems:'center',fontWeight:700}}>{(name||username||'?').slice(0,2).toUpperCase()}</span>}
  <div style={{display:'grid',gap:7}}>
   <strong>Profile picture</strong>
   <input ref={photoInput} type="file" hidden tabIndex={-1} aria-label="Choose profile picture" accept="image/png,image/jpeg,image/webp"
    onChange={e=>{const selected=e.currentTarget.files?.[0];e.currentTarget.value='';void upload(selected);}}/>
   <div style={{display:'flex',flexWrap:'wrap',gap:8}}>
    <button type="button" disabled={busy||photoProcessing||!q.data} onClick={()=>photoInput.current?.click()}>{photo?'Change picture':'Add picture'}</button>
    <button type="button" disabled={busy||!q.data||!photo} onClick={removePhoto}>Remove picture</button>
   </div>
   <small style={{color:'var(--k-dim)'}}>PNG, JPEG or WebP, up to 10 MB. Preview is saved with Save profile.</small>
  </div>
 </div>
 <label>Display name<input aria-label="Profile display name" maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label>
<button disabled={busy||photoProcessing||!q.data} onClick={()=>void run(async()=>{const profile=await api.patch<Profile>('/api/v1/auth/profile',{display_name:name,photo});qc.setQueryData(['sterling-profile'],profile);qc.setQueryData<List>(['sterling-users'], (old?: List) => old ? { ...old, users: old.users.map((u: User) => u.id===profile.user_id ? { ...u, photo: profile.photo } : u) } : old);setMessage('Profile saved.');})}>Save profile</button>
 <details><summary>Username & password</summary><p>Current password is required. Changes sign out your other sessions.</p><label>Username<input aria-label="New Sterling username" value={username} onChange={e=>setUsername(e.target.value)} autoComplete="username"/></label><label>Current password<input aria-label="Current Sterling password" type="password" autoComplete="current-password" value={current} onChange={e=>setCurrent(e.target.value)}/></label><label>New password<input aria-label="New Sterling password" type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><label>Confirm new password<input aria-label="Confirm Sterling password" type="password" autoComplete="new-password" value={confirm} onChange={e=>setConfirm(e.target.value)}/></label><button disabled={busy||!current||(!password&&username===q.data?.username)||password!==confirm||(!!password&&password.length<12)} onClick={()=>void run(async()=>{const result=await api.post<SterlingTokens&{profile:Profile}>('/api/v1/auth/profile/security',{current_password:current,username,new_password:password||null});setAuthTokens(result);qc.setQueryData(['sterling-profile'],result.profile);setCurrent('');setPassword('');setConfirm('');setMessage('Account security updated. Other sessions are signed out.');})}>Update account security</button></details>
 <details><summary>Sessions & sign out</summary><p>Your settings remain saved to your Sterling account.</p><button disabled={busy} onClick={()=>void run(async()=>{await api.post('/api/v1/auth/profile/revoke-sessions');clearAuthTokens();window.location.reload();})}>Sign out all sessions</button><button disabled={busy} onClick={()=>void run(async()=>{try{await disconnectPushDevice();}finally{clearAuthTokens();window.location.reload();}})}>Sign out this browser</button></details><p role="status" aria-live="polite">{message}</p></div></div>,document.body)}</>;
}

import {useState,useEffect} from 'react';
import {useQuery,useMutation,useQueryClient} from '@tanstack/react-query';
import {api} from '../utils/api';
import {useConfigurationCapabilities} from '../hooks/useConfigurationCapabilities';
import {AccessEditor} from '../components/admin/AccessEditor';
import {AccessRequests,type AccessRequest} from '../components/admin/AccessRequests';
import {CentralConnections,BrokerCard} from '../components/admin/CentralConnections';
import {UserTelegram} from '../components/admin/CentralIntegrations';
import {SterlingUsersPanel,type User} from '../components/kite/SterlingUsersPanel';
import type {KiteAccount} from '../types/kite';
import '../styles/administration.css';
type Activity={browser:string;os:string;device:string;ip:string;country:string;last_seen_ms:number;last_login_ms:number|null;online:boolean};
type Person=User&{features:Record<'kite'|'radio'|'tv',boolean>;revision:number;online:boolean;last_login_ms:number|null;activity:Activity[];brokers:KiteAccount[];telegram:unknown[]};
const time=(value:number|null)=>value?new Date(value).toLocaleString():'Not recorded';
export default function Administration(){
  const qc=useQueryClient();
  const capabilities=useConfigurationCapabilities();
  const allowed=!!capabilities.data?.administrator && !!capabilities.data?.active && !capabilities.isError;
  const directory=useQuery({queryKey:['admin-directory'],queryFn:()=>api.get<{users:Person[];brokers:KiteAccount[]}>('/api/v1/admin/directory'),refetchInterval:10000,enabled:allowed});
  const inbox=useQuery({queryKey:['admin-access-requests'],queryFn:()=>api.get<{requests:AccessRequest[]}>('/api/v1/admin/access-requests'),refetchInterval:10000,enabled:allowed});
  const [selected,setSelected]=useState(''),[checked,setChecked]=useState<string[]>([]),[tab,setTab]=useState<'people'|'requests'|'brokers'|'accounts'>('people');
  const users=directory.data?.users||[],person=users.find(u=>u.id===selected);
  useEffect(()=>{if(!selected&&users.length)setSelected(users[0].id);},[selected,users]);
  // Refresh the unified directory after any existing editor saves.
  useEffect(()=>qc.getMutationCache().subscribe(event=>{if(event.type==='updated'&&event.mutation.state.status==='success')void qc.invalidateQueries({queryKey:['admin-directory']});}),[qc]);
  const pending=inbox.data?.requests.filter(r=>r.status==='pending')||[];
  const bulk=useMutation({mutationFn:({feature,enabled}:{feature:string;enabled:boolean})=>api.patch('/api/v1/admin/directory/access',{users:users.filter(u=>checked.includes(u.id)).map(u=>({user_id:u.id,expected_revision:u.revision})),features:{[feature]:enabled}}),onSuccess:()=>{void qc.invalidateQueries({queryKey:['admin-directory']});void qc.invalidateQueries({queryKey:['admin-features']});}});
  if(capabilities.isLoading)return <main className="administration" role="status">Loading administration…</main>;
  if(!allowed)return <main className="administration"><p role="alert">Administrator access required.</p></main>;
  return <main className="administration admin-directory"><div className="directory-heading"><h1>Users</h1><button className="bac-btn" onClick={()=>setTab('accounts')}>Add / manage accounts</button></div>
    <nav className="admin-tabs" aria-label="Administration sections">{([['people','Users'],['requests',`Requests${pending.length?' · '+pending.length:''}`],['brokers','Brokers'],['accounts','Accounts']] as const).map(([id,label])=><button key={id} aria-current={tab===id?'page':undefined} onClick={()=>setTab(id)}>{label}</button>)}</nav>
    {directory.isLoading&&<p role="status">Loading users…</p>}{directory.error&&<p role="alert">{directory.error.message}<button onClick={()=>void directory.refetch()}>Retry</button></p>}
    {tab==='requests'?<AccessRequests requests={inbox.data?.requests||[]}/>:tab==='brokers'?<CentralConnections users={users}/>:tab==='accounts'?<SterlingUsersPanel standalone/>:
    <div className="directory-layout"><section className="directory-people" aria-label="User directory">
      <label className="directory-select-all"><input type="checkbox" checked={users.some(u=>u.active)&&users.filter(u=>u.active).every(u=>checked.includes(u.id))} onChange={e=>setChecked(e.target.checked?users.filter(u=>u.active).map(u=>u.id):[])}/>Select all</label>
      {users.map(user=><div className={`directory-person ${selected===user.id?'selected':''}`} key={user.id}><input aria-label={`Select ${user.username}`} type="checkbox" disabled={!user.active} checked={checked.includes(user.id)} onChange={e=>setChecked(e.target.checked?[...checked,user.id]:checked.filter(id=>id!==user.id))}/><button aria-pressed={selected===user.id} onClick={()=>setSelected(user.id)}><strong>{user.username}</strong><span>{!user.active?'Disabled':user.online?'Online':'Offline'}</span><div className="directory-grants">{(['kite','radio','tv'] as const).filter(f=>user.features[f]).map(f=><b key={f}>{f==='radio'?'Echo':f==='tv'?'Space':'Kite'}</b>)}<b>{user.brokers.length} brokers</b></div></button></div>)}
    </section><section className="directory-detail" aria-label="Selected user">
      {checked.length>0&&<section className="directory-bulk"><h2>{checked.length} selected</h2>{(['kite','radio','tv'] as const).map(feature=><div key={feature}><strong>{feature==='radio'?'Echo':feature==='tv'?'Space':'Kite'}</strong><button disabled={bulk.isPending} onClick={()=>bulk.mutate({feature,enabled:true})}>Enable</button><button disabled={bulk.isPending} onClick={()=>{if(window.confirm(`Revoke ${feature} for ${checked.length} users?`))bulk.mutate({feature,enabled:false});}}>Revoke</button></div>)}{bulk.error&&<p role="alert">{bulk.error.message}</p>}{bulk.isSuccess&&<p role="status">Access updated.</p>}</section>}
      {person&&<><header className="directory-heading"><h2>{person.username}</h2><span>{person.online?'Online':'Offline'}</span></header><dl className="directory-facts"><div><dt>Last login</dt><dd>{time(person.last_login_ms)}</dd></div><div><dt>Brokers</dt><dd>{person.brokers.map(b=>b.label).join(', ')||'None assigned'}</dd></div><div><dt>Telegram</dt><dd>{person.telegram.length} destinations</dd></div></dl>
        {pending.some(r=>r.user_id===person.id)&&<AccessRequests requests={pending.filter(r=>r.user_id===person.id)}/>}
        <AccessEditor key={person.id} userId={person.id}/>
        <details><summary>Broker assignments · {person.brokers.length}</summary>{directory.data?.brokers.map(broker=><BrokerCard key={`${person.id}:${broker.id}`} broker={broker} users={users} targetUser={person.id}/>)}{!directory.data?.brokers.length&&<p>No broker connections.</p>}</details>
        <details><summary>Telegram · {person.telegram.length}</summary><UserTelegram key={person.id} userId={person.id}/></details>
        <details open><summary>Login activity</summary>{person.activity.length?<div className="directory-activity">{person.activity.map((a,i)=><article key={i}><strong>{a.browser} · {a.os} · {a.device}</strong><span>{a.online?'Online':'Last seen '+time(a.last_seen_ms)}</span><span>{a.ip}{a.country?' · '+a.country:''}</span><span>Login {time(a.last_login_ms)}</span></article>)}</div>:<p>No activity recorded.</p>}</details>
      </>}
    </section></div>}
    {inbox.error&&<p role="alert">{inbox.error.message}</p>}
  </main>;
}

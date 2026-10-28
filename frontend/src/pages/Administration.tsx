import {useState,useEffect} from 'react';
import {useQuery,useMutation,useQueryClient} from '@tanstack/react-query';
import {api} from '../utils/api';
import {useConfigurationCapabilities} from '../hooks/useConfigurationCapabilities';
import {AccessEditor} from '../components/admin/AccessEditor';
import {AccessRequests,type AccessRequest} from '../components/admin/AccessRequests';
import {CentralConnections,BrokerCard} from '../components/admin/CentralConnections';
import {UserTelegram} from '../components/admin/CentralIntegrations';
import {SterlingUsersPanel,type User} from '../components/kite/SterlingUsersPanel';
import {useConfirm} from '../components/kite/ConfirmDialog';
import {hasUnsavedDraft} from '../components/kite/config/unsavedDraftGuard';
import type {KiteAccount} from '../types/kite';
import '../styles/administration.css';

type Feature='kite'|'radio'|'tv';
type Activity={browser:string;os:string;device:string;ip:string;country:string;last_seen_ms:number;last_login_ms:number|null;online:boolean};
type Person=User&{features:Record<Feature,boolean>;revision:number;online:boolean;last_login_ms:number|null;activity:Activity[];brokers:KiteAccount[];telegram:unknown[]};
type Directory={users:Person[];brokers:KiteAccount[];current_user_id:string};
type Section='people'|'requests'|'brokers'|'accounts';
type Detail='overview'|'access'|'brokers'|'telegram'|'activity';
const features:Feature[]=['kite','radio','tv'];
const names:Record<Feature,string>={kite:'Kite',radio:'Echo',tv:'Space'};
const time=(value:number|null)=>value?new Date(value).toLocaleString():'Not recorded';
function Avatar({person}:{person:Person}){return person.photo?<img className="directory-avatar" src={person.photo} alt=""/>:<span className="directory-avatar" aria-hidden="true">{person.username.slice(0,2).toUpperCase()}</span>;}

export default function Administration(){
  const qc=useQueryClient(),confirm=useConfirm();
  const capabilities=useConfigurationCapabilities();
  const allowed=!!capabilities.data?.administrator&&!!capabilities.data?.active&&!capabilities.isError;
  const directory=useQuery({queryKey:['admin-directory'],queryFn:()=>api.get<Directory>('/api/v1/admin/directory'),refetchInterval:10000,enabled:allowed});
  const inbox=useQuery({queryKey:['admin-access-requests'],queryFn:()=>api.get<{requests:AccessRequest[]}>('/api/v1/admin/access-requests'),refetchInterval:10000,enabled:allowed});
  const [selected,setSelected]=useState(''),[checked,setChecked]=useState<string[]>([]);
  const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[creating,setCreating]=useState(false),[createKey,setCreateKey]=useState(0);
  const [section,setSection]=useState<Section>('people'),[detail,setDetail]=useState<Detail>('overview'),[personOpen,setPersonOpen]=useState(false);
  const users=directory.data?.users||[],person=users.find(user=>user.id===selected);
  const shown=users.filter(user=>(status==='all'||(status==='active'?user.active:!user.active))&&[user.username,user.id,...user.brokers.map(b=>b.kite_user_id||b.label)].join(' ').toLowerCase().includes(search.trim().toLowerCase()));
  const pending=inbox.data?.requests.filter(request=>request.status==='pending')||[];
  const selectedUsers=users.filter(user=>user.active&&checked.includes(user.id));
  useEffect(()=>{if(users.length&&!users.some(user=>user.id===selected))setSelected(users[0].id);},[users,selected]);
  useEffect(()=>qc.getMutationCache().subscribe(event=>{
    if(event.type==='updated'&&event.mutation.state.status==='success')void qc.invalidateQueries({queryKey:['admin-directory']});
  }),[qc]);
  const bulk=useMutation({mutationFn:({feature,enabled}:{feature:Feature;enabled:boolean})=>api.patch<{updated:number}>('/api/v1/admin/directory/access',{
    users:selectedUsers.map(user=>({user_id:user.id,expected_revision:user.revision})),features:{[feature]:enabled},
  }),onSuccess:()=>{void qc.invalidateQueries({queryKey:['admin-directory']});void qc.invalidateQueries({queryKey:['admin-features']});}});
  async function leave(){return !hasUnsavedDraft()||await confirm({title:'Discard unsaved changes?',confirmLabel:'Discard',danger:true});}
  async function chooseUser(id:string){if((id!==selected||detail!=='overview')&&!await leave())return;setSelected(id);setDetail('overview');setPersonOpen(true);}
  async function openCreate(){if(!await leave())return;setCreating(true);setCreateKey(key=>key+1);setSection('accounts');}
  async function chooseSection(next:Section){if((next!==section||next==='people')&&!await leave())return;setSection(next);setCreating(false);if(next==='people'){setPersonOpen(false);setDetail('overview');}}
  async function chooseDetail(next:Detail){if(next!==detail&&!await leave())return;setDetail(next);}
  async function changeBulk(feature:Feature,enabled:boolean){
    if(!enabled&&!await confirm({title:`Revoke ${names[feature]} access?`,body:selectedUsers.map(user=>user.username).join(', '),confirmLabel:'Revoke access',danger:true}))return;
    bulk.mutate({feature,enabled});
  }
  if(capabilities.isLoading)return <main className="administration" role="status">Loading administration…</main>;
  if(capabilities.isError)return <main className="administration"><p role="alert">Administration unavailable.</p><button className="bac-btn" onClick={()=>void capabilities.refetch()}>Retry</button></main>;
  if(!allowed)return <main className="administration"><p role="alert">Administrator access required.</p></main>;
  return <main className="administration admin-directory" data-person-open={personOpen}>
    <header className="directory-heading"><div><span className="admin-brand">STERLING · ADMINISTRATOR</span><h1>Administration</h1><p>People, access and connections in one place.</p></div><button className="bac-btn bac-btn-primary" onClick={()=>void openCreate()}>Add user</button></header>
    <section className="admin-summary" aria-label="Administration overview"><div><strong>{users.length}</strong><span>Total users</span></div><div><strong>{users.filter(u=>u.active).length}</strong><span>Active logins</span></div><div><strong>{users.filter(u=>u.online).length}</strong><span>Online now</span></div><button onClick={()=>void chooseSection('requests')}><strong>{pending.length}</strong><span>Pending requests</span></button></section>
    <nav className="admin-tabs" aria-label="Administration sections">
      {([['people',`Users · ${users.length}`],['requests',`Requests${pending.length?' · '+pending.length:''}`],['brokers','Brokers'],['accounts','Login accounts']] as const).map(([id,label])=><button type="button" key={id} aria-current={section===id?'page':undefined} onClick={()=>void chooseSection(id)}>{label}</button>)}
    </nav>
    {directory.isLoading&&<p role="status">Loading users…</p>}
    {directory.error&&<p role="alert">{directory.error.message} <button onClick={()=>void directory.refetch()}>Retry</button></p>}
    {section==='people'&&selectedUsers.length>0&&<section className="directory-bulk" aria-label="Bulk access"><header><h2>{selectedUsers.length} selected</h2><button type="button" onClick={()=>setChecked([])}>Clear</button></header>{features.map(feature=><div key={feature}><strong>{names[feature]}</strong><button disabled={bulk.isPending} onClick={()=>void changeBulk(feature,true)}>Enable</button><button disabled={bulk.isPending} onClick={()=>void changeBulk(feature,false)}>Revoke</button></div>)}{bulk.error&&<p role="alert">{bulk.error.message}</p>}{bulk.isSuccess&&<p role="status">Access updated for {bulk.data.updated} users.</p>}</section>}
    {section==='requests'?<AccessRequests requests={inbox.data?.requests||[]}/>:section==='brokers'?<CentralConnections users={users}/>:section==='accounts'?<SterlingUsersPanel key={createKey} standalone initialCreate={creating} onCreated={async id=>{await directory.refetch();setSelected(id);setSection('people');setPersonOpen(true);setDetail('overview');setCreating(false);}}/>:
    <div className="directory-layout">
      <section className="directory-people" aria-label="User directory">
        <div className="directory-search"><label>Find a user<input type="search" placeholder="Name or broker identity" value={search} onChange={e=>setSearch(e.target.value)}/></label><label>Account status<select value={status} onChange={e=>setStatus(e.target.value)}><option value="all">All accounts</option><option value="active">Active logins</option><option value="suspended">Suspended logins</option></select></label></div>
        <label className="directory-select-all"><input type="checkbox" disabled={bulk.isPending} checked={shown.some(user=>user.active)&&shown.filter(user=>user.active).every(user=>checked.includes(user.id))} onChange={event=>setChecked(event.target.checked?shown.filter(user=>user.active).map(user=>user.id):[])}/>Select all</label>
        {!shown.length&&<p className="admin-empty">{users.length?'No users match your search.':'No users to display.'}</p>}
        {shown.map(user=><div className={`directory-person ${selected===user.id?'selected':''}`} key={user.id}>
          <input aria-label={`Select ${user.username}`} type="checkbox" disabled={!user.active||bulk.isPending} checked={checked.includes(user.id)} onChange={event=>setChecked(event.target.checked?[...checked,user.id]:checked.filter(id=>id!==user.id))}/>
          <button type="button" aria-pressed={selected===user.id} onClick={()=>void chooseUser(user.id)}><Avatar person={user}/><span className="directory-person-text"><strong>{user.username}</strong><span className="directory-presence" data-online={user.online}>{!user.active?'Suspended':user.online?'Online':'Offline'}</span><span className="directory-grants">{user.primary?<b>Administrator</b>:features.filter(feature=>user.features[feature]).map(feature=><b key={feature}>{names[feature]}</b>)}</span></span></button>
        </div>)}
      </section>
      <section className="directory-detail" aria-label="Selected user">
        <button type="button" className="directory-back bac-btn" onClick={()=>{if(!hasUnsavedDraft())setPersonOpen(false);else void leave().then(ok=>{if(ok)setPersonOpen(false);});}}>‹ Users</button>

        {!person&&!directory.isLoading&&<div className="admin-empty"><h2>Select a user</h2><p>Review workspace access, settings permissions, connections and activity.</p></div>}
        {person&&<>
          <header className="directory-user-heading"><Avatar person={person}/><div><h2>{person.username}</h2><span>{person.primary?'Administrator':person.active?'Active account':'Suspended'}</span></div><span className="directory-presence" data-online={person.online}>{person.online?'Online':'Offline'}</span></header>
          <nav className="directory-detail-tabs" aria-label="User management">{([['overview','Overview'],['access','Access'],['brokers',`Brokers · ${person.brokers.length}`],['telegram',`Telegram · ${person.telegram.length}`],['activity','Activity']] as const).map(([id,label])=><button type="button" key={id} aria-current={detail===id?'page':undefined} onClick={()=>void chooseDetail(id)}>{label}</button>)}</nav>
          {pending.some(request=>request.user_id===person.id)&&<AccessRequests requests={pending.filter(request=>request.user_id===person.id)}/>}
          {detail==='overview'&&<div className="directory-overview">
            <dl className="directory-facts"><div><dt>Last login</dt><dd>{time(person.last_login_ms)}</dd></div><div><dt>Last active</dt><dd>{time(person.activity[0]?.last_seen_ms||null)}</dd></div><div><dt>Browser / device</dt><dd>{person.activity[0]?[person.activity[0].browser,person.activity[0].device].join(' · '):'Not recorded'}</dd></div><div><dt>IP / country</dt><dd>{person.activity[0]?[person.activity[0].ip,person.activity[0].country].filter(Boolean).join(' · '):'Not recorded'}</dd></div></dl>
            <section><div className="directory-section-heading"><h3>Access</h3><button onClick={()=>void chooseDetail('access')}>Edit access</button></div><div className="directory-access-summary">{features.map(feature=><span key={feature} data-enabled={person.features[feature]}>{names[feature]}<strong>{person.features[feature]?'Enabled':'Disabled'}</strong></span>)}</div></section>
            <section><div className="directory-section-heading"><h3>Broker connections</h3><button onClick={()=>void chooseDetail('brokers')}>Manage assignments</button></div>{person.brokers.length?person.brokers.map(broker=><article className="directory-broker-summary" key={broker.id}><strong>{broker.label}</strong><span>{broker.kite_user_id||'Identity not verified'}</span><span>{broker.connected?'Session stored':'Disconnected'}</span><span>Assigned to {person.username}</span></article>):<p>None assigned</p>}</section>
            <section><div className="directory-section-heading"><h3>Telegram</h3><button onClick={()=>void chooseDetail('telegram')}>Manage destinations</button></div><p>{person.telegram.length} destinations</p></section>
          </div>}
          {detail==='access'&&<AccessEditor key={person.id} userId={person.id}/>}
          {detail==='brokers'&&<div>{directory.data?.brokers.map(broker=><BrokerCard key={`${person.id}:${broker.id}`} broker={broker} users={users} targetUser={person.id}/>)}{!directory.data?.brokers.length&&<p>No broker connections.</p>}</div>}
          {detail==='telegram'&&<UserTelegram key={person.id} userId={person.id}/>}
          {detail==='activity'&&(person.activity.length?<div className="directory-activity">{person.activity.map((activity,index)=><article key={index}><strong>{activity.browser} · {activity.os} · {activity.device}</strong><span>{activity.online?'Online':'Last seen '+time(activity.last_seen_ms)}</span><span>{activity.ip}{activity.country?' · '+activity.country:''}</span><span>Login {time(activity.last_login_ms)}</span></article>)}</div>:<p>No activity recorded.</p>)}
        </>}
      </section>
    </div>}
    {inbox.error&&<p role="alert">{inbox.error.message}</p>}
  </main>;
}

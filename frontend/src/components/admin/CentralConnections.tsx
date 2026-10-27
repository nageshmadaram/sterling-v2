import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import type { User } from '../kite/SterlingUsersPanel';
import type { KiteAccount } from '../../types/kite';

const BASE = '/api/v1/admin/brokers';
export function BrokerCard({ broker, users, targetUser }: { broker: KiteAccount; users: User[]; targetUser?:string }) {
  const qc = useQueryClient();
  const loginStatus=useQuery({queryKey:['admin-broker-login',broker.id,broker.user_id],queryFn:()=>api.get<{password_saved:boolean}>(BASE+'/'+encodeURIComponent(broker.id)+'/login-credentials')});
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(broker.label);
  const [key, setKey] = useState(''), [secret, setSecret] = useState('');
  const [loginUser,setLoginUser]=useState(broker.kite_user_id||''),[loginPassword,setLoginPassword]=useState('');
  const login=useMutation({mutationFn:()=>api.put(BASE+'/'+encodeURIComponent(broker.id)+'/login-credentials',{username:loginUser,password:loginPassword,expected_user_id:broker.user_id,expected_updated_at_ms:broker.updated_at_ms}),onSuccess:()=>{setLoginPassword('');setNotice('Kite login credentials saved.');void qc.invalidateQueries({queryKey:['admin-broker-login',broker.id]});},onError:()=>setLoginPassword('')});
  const [notice, setNotice] = useState('');
  const [assignee, setAssignee] = useState(targetUser && targetUser!==broker.user_id?targetUser:'');
  const assign = useMutation({ mutationFn: () => api.post(BASE + '/' + encodeURIComponent(broker.id) + '/assignment', { user_id: assignee, expected_user_id: broker.user_id, expected_updated_at_ms: broker.updated_at_ms }), onSuccess: () => { setNotice('Assigned to '+(users.find(u=>u.id===assignee)?.username||assignee)+'. Broker sign-in is required.'); setAssignee(''); void qc.invalidateQueries({ queryKey: ['admin-brokers'] }); }, });
  const save = useMutation({ mutationFn: () => api.patch(BASE + '/' + encodeURIComponent(broker.id), {
    label, expected_updated_at_ms: broker.updated_at_ms, ...(key.trim() ? { api_key: key.trim() } : {}), ...(secret.trim() ? { api_secret: secret.trim() } : {}),
  }), onSuccess: () => { setKey(''); setSecret(''); setEditing(false); setNotice('Connection saved. Reconnect from the assigned Kite workspace if credentials changed.'); void qc.invalidateQueries({ queryKey: ['admin-brokers'] }); },
  onError: () => { setKey(''); setSecret(''); } });
  const remove = useMutation({ mutationFn: () => api.delete(BASE + '/' + encodeURIComponent(broker.id)),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-brokers'] }) });
  return <article className="admin-connection"><div className="admin-connection-heading"><div><h3>{broker.label}</h3>
    <p>Assigned to {users.find(user => user.id === broker.user_id)?.username || broker.user_id}</p></div>
    <span className="admin-status">{broker.connected ? 'Session stored' : 'Not signed in'}</span></div>
    <dl><dt>Broker identity</dt><dd>{broker.kite_user_id || 'Awaiting broker verification'}</dd><dt>Automatic login</dt><dd>{loginStatus.isError?'Unavailable':loginStatus.isLoading?'Checking…':loginStatus.data?.password_saved?'Credentials saved':'Password not saved'}</dd><dt>API credentials</dt><dd>{broker.has_credentials ? 'Configured · masked' : 'Not configured'}</dd></dl>
    <div className="admin-checks"><button className="bac-btn" onClick={() => { setEditing(!editing); setKey(''); setSecret(''); }}> {editing ? 'Cancel edit' : 'Edit connection'}</button>
      <button className="bac-btn" disabled={remove.isPending} onClick={() => { if (window.confirm(`Remove ${broker.label}? Removal is refused unless exposure and pending broker activity are safely clear.`)) remove.mutate(); }}>Remove connection</button></div>
    <details className="admin-assign-panel"><summary>Assign to a Sterling user</summary><p>Map this Kite profile to another Sterling user. Reassignment requires a verified flat broker account with no pending activity. The broker session is cleared; the new user signs in again.</p>
      <div className="admin-assignees">{users.filter(user=>user.active).map(user=><button type="button" key={user.id} disabled={user.id===broker.user_id} aria-pressed={assignee===user.id} onClick={()=>setAssignee(user.id)}>{user.username}{user.id===broker.user_id?' · Assigned':''}</button>)}</div>
      <button type="button" className="bac-btn bac-btn-primary" disabled={!assignee || assign.isPending} onClick={() => { if(window.confirm(`Assign ${broker.label} to ${users.find(u=>u.id===assignee)?.username || assignee}? The broker session will be cleared.`)) assign.mutate(); }}>{assign.isPending ? 'Verifying assignment…' : 'Save assignment'}</button>
      {assign.error && <p role="alert">{assign.error.message}</p>}
    </details>
    <details><summary>Kite automatic login</summary><form className="admin-secret-form" onSubmit={e=>{e.preventDefault();login.mutate();}}><label>Kite user ID<input required value={loginUser} onChange={e=>setLoginUser(e.target.value)} autoComplete="off"/></label><label>Kite password<input type="password" required value={loginPassword} onChange={e=>setLoginPassword(e.target.value)} autoComplete="new-password"/></label><button disabled={login.isPending}>Save login credentials</button>{login.error&&<p role="alert">{login.error.message}</p>}</form></details>
    {editing && <form className="admin-secret-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}>
      <label>Connection label<input className="bac-input" required maxLength={100} value={label} onChange={event => setLabel(event.target.value)} /></label>
      <label>Replacement API key<input className="bac-input" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} placeholder="Blank keeps saved key" /></label>
      <label>Replacement API secret<input className="bac-input" type="password" autoComplete="new-password" value={secret} onChange={event => setSecret(event.target.value)} placeholder="Blank keeps saved secret" /></label>
      <p>Existing secrets are never loaded into this form. Credential changes require verified safe broker state.</p>
      <button className="bac-btn bac-btn-primary" disabled={save.isPending}>Save connection</button>
    </form>}
    {(save.error || remove.error) && <p role="alert">{(save.error || remove.error)?.message}</p>}{notice && <p role="status">{notice}</p>}
  </article>;
}
export function CentralConnections({ users }: { users: User[] }) {
  const qc = useQueryClient();
  const brokers = useQuery({ queryKey: ['admin-brokers'], queryFn: () => api.get<{ brokers: KiteAccount[] }>(BASE), retry: false });
  const [owner, setOwner] = useState(''), [label, setLabel] = useState(''), [key, setKey] = useState(''), [secret, setSecret] = useState('');
  const create = useMutation({ mutationFn: () => api.post(BASE, { user_id: owner, label, api_key: key, api_secret: secret }),
    onSuccess: () => { setLabel(''); setKey(''); setSecret(''); void qc.invalidateQueries({ queryKey: ['admin-brokers'] }); },
    onError: () => { setKey(''); setSecret(''); } });
  return <section className="admin-wide"><h2>Broker connections</h2>
    <div className="admin-connections-layout"><div>{brokers.isLoading && <p role="status">Loading connections…</p>}{brokers.isError && <p role="alert">{brokers.error.message}</p>}
      {brokers.data?.brokers.map(broker => <BrokerCard key={broker.id} broker={broker} users={users} />)}
      {brokers.data?.brokers.length === 0 && <p>No broker connections have been registered.</p>}</div>
      <form className="admin-connection admin-secret-form" onSubmit={event => { event.preventDefault(); create.mutate(); }}><h3>Add a broker connection</h3>
        <fieldset className="admin-assignees"><legend>Assign to user</legend>{users.filter(user=>user.active).map(user=><button type="button" key={user.id} aria-pressed={owner===user.id} onClick={()=>setOwner(user.id)}>{user.username}</button>)}</fieldset>
        <label>Connection label<input className="bac-input" required maxLength={100} value={label} onChange={event => setLabel(event.target.value)} /></label>
        <label>API key<input className="bac-input" required maxLength={512} autoComplete="off" value={key} onChange={event => setKey(event.target.value)} /></label>
        <label>API secret<input className="bac-input" type="password" required maxLength={512} autoComplete="new-password" value={secret} onChange={event => setSecret(event.target.value)} /></label>
        <p>The user also needs a separate Kite feature grant before connecting.</p><button className="bac-btn bac-btn-primary" disabled={create.isPending || !owner}>Save broker connection</button>
        {create.isError && <p role="alert">{create.error.message}</p>}{create.isSuccess && <p role="status">Connection saved. The assigned user can connect after choosing Kite.</p>}
      </form></div>
  </section>;
}

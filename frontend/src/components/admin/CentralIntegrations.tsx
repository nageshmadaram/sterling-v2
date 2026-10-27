import { UserCombobox } from './UserCombobox';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import type { User } from '../kite/SterlingUsersPanel';
type Target = { id: string; label: string; chat_id: string; enabled: boolean; bot_token_set: boolean };
function UserTelegram({ userId }: { userId: string }) {
  const qc = useQueryClient();
  const path = `/api/v1/admin/users/${encodeURIComponent(userId)}/telegram`;
  const query = useQuery({ queryKey: ['admin-telegram', userId], queryFn: () => api.get<{ targets: Target[] }>(path) });
  const [label, setLabel] = useState(''), [token, setToken] = useState(''), [chat, setChat] = useState('');
  const save = useMutation({ mutationFn: () => api.post(path, { label, bot_token: token, chat_id: chat }),
    onSuccess: () => { setLabel(''); setToken(''); setChat(''); void qc.invalidateQueries({ queryKey: ['admin-telegram', userId] }); }, onError: () => setToken('') });
  const remove = useMutation({ mutationFn: (id: string) => api.delete(path + '/' + encodeURIComponent(id)), onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-telegram', userId] }) });
  return <div className="admin-connections-layout"><div>{query.isLoading && <p role="status">Loading destinations…</p>}{query.isError && <p role="alert">{query.error.message}</p>}
    {query.data?.targets.map(target => <article className="admin-connection" key={target.id}><h3>{target.label}</h3><p>Recipient: {target.chat_id}</p><p>{target.enabled ? 'Enabled' : 'Disabled'} · Bot token {target.bot_token_set ? 'saved and masked' : 'not configured'}</p>
      <button className="bac-btn" disabled={remove.isPending} onClick={() => { if (window.confirm(`Remove destination ${target.label}?`)) remove.mutate(target.id); }}>Remove destination</button></article>)}
    {query.data?.targets.length === 0 && <p>No Telegram destinations are configured for this user.</p>}{remove.error && <p role="alert">{remove.error.message}</p>}</div>
    <form className="admin-connection admin-secret-form" onSubmit={event => { event.preventDefault(); save.mutate(); }}><h3>Add Telegram destination</h3>
      <label>Destination label<input className="bac-input" required maxLength={100} value={label} onChange={e => setLabel(e.target.value)} /></label>
      <label>Bot token<input className="bac-input" type="password" required autoComplete="new-password" maxLength={512} value={token} onChange={e => setToken(e.target.value)} /></label>
      <label>Recipient chat ID<input className="bac-input" required maxLength={128} value={chat} onChange={e => setChat(e.target.value)} /></label>
      <p>Saving does not send a message. This configures an alert destination; the platform control bot remains separate.</p>
      <button className="bac-btn bac-btn-primary" disabled={save.isPending}>Save destination</button>
      {save.error && <p role="alert">{save.error.message}</p>}{save.isSuccess && <p role="status">Destination saved. No test message was sent.</p>}
    </form></div>;
}
export function CentralIntegrations({ users }: { users: User[] }) {
  const [selected, setSelected] = useState('');
  return <section className="admin-wide"><h2>Telegram integrations</h2><p>Manage alert credentials and recipients for Sterling users.</p>
    <UserCombobox label="Integration owner" users={users.filter(user => user.active)} value={selected} onChange={setSelected} />
    {selected ? <UserTelegram key={selected} userId={selected} /> : <p>Select a user to manage their Telegram destinations.</p>}
  </section>;
}

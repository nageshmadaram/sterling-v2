import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useIsSimulation } from '../../store/useViewStore';

type User = { id: string; username: string; role: 'admin' | 'user'; active: boolean; password_set: boolean; primary: boolean };
type List = { current_user_id: string; users: User[] };
type Draft = { id?: string; username: string; password: string; repeat: string; role: 'admin' | 'user'; active: boolean };
const BASE = '/api/v1/auth/users';
const KEY = ['sterling-users'];
const field = { padding: 10, border: '1px solid var(--k-border)', borderRadius: 7, background: 'var(--k-bg)', color: 'var(--k-text)', font: 'inherit' };

export function SterlingUsersPanel() {
  const simulation = useIsSimulation();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: KEY, queryFn: () => api.get<List>(BASE), enabled: !simulation });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const save = useMutation({ mutationFn: (value: Draft) => {
    const { id, repeat: _repeat, ...body } = value;
    return id ? api.put<User>(`${BASE}/${encodeURIComponent(id)}`, body) : api.post<User>(BASE, body);
  }, onSuccess: (_result, value) => {
    setDraft(null); setVisible(false); qc.invalidateQueries({ queryKey: KEY });
    setNotice(value.id === query.data?.current_user_id ? 'Sterling login updated. Sign in again with your updated username and password.' : 'User saved. Existing sessions for an edited user are signed out.');
  }, onError: (e: Error) => { setError(e.message); setVisible(false); setDraft(previous => previous ? { ...previous, password: '', repeat: '' } : null); } });
  const remove = useMutation({ mutationFn: (id: string) => api.delete(`${BASE}/${encodeURIComponent(id)}`), onSuccess: () => {
    qc.invalidateQueries({ queryKey: KEY }); setNotice('Login deleted. Its identity is retained for audit history; you can restore it by enabling the login.');
  }, onError: (e: Error) => setError(e.message) });
  const edit = (user?: User) => {
    setError(''); setNotice(''); setVisible(false);
    setDraft({ id: user?.id, username: user?.username || '', password: '', repeat: '', role: user?.role || 'user', active: user?.active ?? true });
  };
  const busy = save.isPending || remove.isPending;
  const locked = draft?.id === query.data?.current_user_id || query.data?.users.some(u => u.id === draft?.id && u.primary);
  const patch = (value: Partial<Draft>) => setDraft(previous => previous ? { ...previous, ...value } : null);
  if (simulation) return <p>User management is available in Live view.</p>;
  if ((query.error as (Error & { status?: number }) | null)?.status === 403) return <p>Sterling user administration is available to administrators. Your linked broker accounts are managed below.</p>;
  return <section className="settings-account-users account-users" style={{ padding: 0, color: 'var(--k-text)' }}>
    <div className="account-section-heading"><div><h2>Sterling users</h2><span className="account-caption">Workspace access</span></div>{query.data && <button style={field} disabled={busy} onClick={() => edit()}>Add user</button>}</div>
    {query.isLoading && <p role="status">Loading users…</p>}
    {query.isError && <p role="alert">{(query.error as Error).message}</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {query.data && <>
      <ul className="account-user-list">
        {query.data.users.map(user => <li key={user.id} className="account-user-row">
          <span className="account-avatar" aria-hidden="true">{user.username.slice(0, 2).toUpperCase()}</span>
          <div className="account-user-identity"><strong>{user.username}</strong><span>{user.role === 'admin' ? 'Administrator' : 'User'}{user.id === query.data.current_user_id ? ' · You' : ''}</span></div>
          <span className={`account-state ${user.active ? 'is-on' : ''}`}>{user.active ? 'Enabled' : 'Disabled'}</span>
          <button style={field} disabled={busy} onClick={() => edit(user)}>Edit</button>
          {user.active && !user.primary && user.id !== query.data.current_user_id && <details className="account-more"><summary aria-label={`More actions for ${user.username}`}>···</summary><button style={field} disabled={busy} onClick={() => { if (window.confirm(`Delete ${user.username}’s login? It will be signed out. Audit identity is retained. Users with configured broker accounts must remove them first.`)) remove.mutate(user.id); }}>Delete login</button></details>}
        </li>)}
      </ul>
    </>}
    {draft && <form onSubmit={event => {
      event.preventDefault(); setError('');
      if (draft.password !== draft.repeat) { setError('Passwords do not match.'); return; }
      if ((!draft.id || draft.password) && draft.password.length < 12) { setError('Use at least 12 characters for the password.'); return; }
      if (draft.id === query.data?.current_user_id && !window.confirm('Update your Sterling login? Existing app sessions will be signed out. Your broker session stays connected.')) return;
      save.mutate(draft);
    }} style={{ marginTop: 18, padding: 16, border: '1px solid var(--k-border)', borderRadius: 9, display: 'grid', gap: 12, maxWidth: 520 }}>
      <h3>{draft.id ? 'Update Sterling user' : 'Add Sterling user'}</h3>
      <label>Username<input style={{ ...field, display: 'block', width: '100%', boxSizing: 'border-box' }} required minLength={3} maxLength={64} value={draft.username} autoComplete="username" onChange={event => patch({ username: event.target.value })} /></label>
      <label>Access<select aria-label="Access" style={{ ...field, marginLeft: 8 }} disabled={locked} value={draft.role} onChange={event => patch({ role: event.target.value as 'admin' | 'user' })}><option value="user">User · own workspace</option><option value="admin">Administrator · can manage users</option></select></label>
      <label>New password {draft.id ? '(blank keeps saved password)' : ''}<div style={{ display: 'flex', gap: 8 }}><input aria-label="New password" style={{ ...field, flex: 1, minWidth: 0 }} type={visible ? 'text' : 'password'} required={!draft.id} maxLength={256} value={draft.password} autoComplete="new-password" onChange={event => patch({ password: event.target.value })} />
        <button style={field} type="button" aria-label={visible ? 'Hide new password' : 'Show new password'} onClick={() => setVisible(!visible)}>{visible ? 'Hide' : 'Show'}</button></div></label>
      <label>Repeat new password<input style={{ ...field, display: 'block', width: '100%', boxSizing: 'border-box' }} type="password" value={draft.repeat} autoComplete="new-password" onChange={event => patch({ repeat: event.target.value })} /></label>
      <label><input type="checkbox" checked={draft.active} disabled={locked} onChange={event => patch({ active: event.target.checked })} /> Login enabled</label>
      <div><button style={field} disabled={busy}>{save.isPending ? 'Saving…' : 'Save user'}</button><button type="button" style={{ ...field, marginLeft: 8 }} disabled={busy} onClick={() => { setDraft(null); setVisible(false); }}>Cancel</button></div>
    </form>}
  </section>;
}

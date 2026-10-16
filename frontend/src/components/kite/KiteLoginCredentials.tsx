import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';

type Saved = { username: string; password_saved: boolean; automatic_login_available: boolean };
const field = { padding: 10, border: '1px solid var(--k-border)', borderRadius: 7, background: 'var(--k-bg)', color: 'var(--k-text)', font: 'inherit' };
const input = { ...field, display: 'block', width: '100%', boxSizing: 'border-box' as const };

export function KiteLoginCredentials({ accountId }: { accountId: string }) {
  const url = `/api/v1/kite/accounts/${encodeURIComponent(accountId)}/login-credentials`;
  const queryKey = ['kite-login-credentials', accountId];
  const client = useQueryClient();
  const saved = useQuery({ queryKey, queryFn: () => api.get<Saved>(url), retry: false });
  const [editing, setEditing] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const clearDraft = () => { setEditing(false); setUsername(''); setPassword(''); setRepeat(''); setVisible(false); setError(''); };
  const save = useMutation({ mutationFn: () => api.put<Saved>(url, { username, password }), onSuccess: data => {
    client.setQueryData(queryKey, data); clearDraft(); setNotice('Login details saved.');
  }, onError: () => { setPassword(''); setRepeat(''); setVisible(false); setNotice(''); } });
  const remove = useMutation({ mutationFn: () => api.delete(url), onSuccess: () => {
    client.setQueryData(queryKey, { username: '', password_saved: false, automatic_login_available: false });
    clearDraft(); setNotice('Saved Kite login removed. Your existing broker session is unchanged.');
  } });
  const busy = save.isPending || remove.isPending;
  const edit = () => {
    save.reset(); remove.reset(); setNotice(''); setError(''); setVisible(false);
    setUsername(saved.data?.username ?? ''); setPassword(''); setRepeat(''); setEditing(true);
  };
  return <section aria-label="Saved Kite login credentials" className="settings-account-users" style={{ padding: 0, color: 'var(--k-text)' }}>
    <h3 className="account-subheading">Saved login</h3>
    {saved.isPending && <p role="status">Loading saved Kite login…</p>}
    {saved.error && <p role="alert">{saved.error.message}</p>}
    {(error || save.error || remove.error) && <p role="alert">{error || save.error?.message || remove.error?.message}</p>}
    {notice && <p role="status">{notice}</p>}
    {saved.data && <>
      {!saved.data.password_saved && !editing && <button type="button" style={field} disabled={busy} onClick={edit}>Add Kite login</button>}
      <div className="account-credential-row">
        <div><strong>{saved.data.username || 'No user ID'}</strong><span className="account-caption">{saved.data.password_saved ? 'Password saved' : 'Password not set'}</span></div>
        {saved.data.password_saved && <div className="account-row-actions">
          <button type="button" style={field} disabled={busy} onClick={edit}>Edit login</button>
          <details className="account-more"><summary aria-label="More saved login actions">···</summary><button type="button" style={field} disabled={busy} onClick={() => {
            if (window.confirm('Remove this saved Kite username and password? Your current broker session will stay connected.')) remove.mutate();
          }}>Remove saved login</button></details>
        </div>}
      </div>
      {editing && <form onSubmit={event => {
        event.preventDefault(); setError('');
        if (password !== repeat) { setError('Passwords do not match.'); return; }
        save.mutate();
      }} style={{ marginTop: 18, padding: 16, border: '1px solid var(--k-border)', borderRadius: 9, display: 'grid', gap: 12, maxWidth: 520 }}>
        <h3>{saved.data.password_saved ? 'Update Kite login' : 'Add Kite login'}</h3><p className="account-caption">Updates the copy saved in Sterling.</p>
        <label>Kite user ID<input aria-label="Kite user ID" style={input} required disabled={busy} maxLength={64} autoComplete="off" value={username} onChange={event => setUsername(event.target.value)} /></label>
        <label>New password {saved.data.password_saved ? '(blank keeps saved password)' : ''}<div style={{ display: 'flex', gap: 8 }}>
          <input aria-label="New Kite password" style={{ ...field, flex: 1, minWidth: 0 }} type={visible ? 'text' : 'password'} disabled={busy} maxLength={512} required={!saved.data.password_saved} autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} />
          <button type="button" style={field} disabled={busy} aria-label={visible ? 'Hide new Kite password' : 'Show new Kite password'} onClick={() => setVisible(!visible)}>{visible ? 'Hide' : 'Show'}</button>
        </div></label>
        <label>Repeat new password<input aria-label="Repeat new Kite password" style={input} type="password" disabled={busy} maxLength={512} autoComplete="new-password" value={repeat} onChange={event => setRepeat(event.target.value)} /></label>
        <div><button type="submit" style={field} disabled={busy}>{save.isPending ? 'Saving…' : 'Save Kite login'}</button>
          <button type="button" style={{ ...field, marginLeft: 8 }} disabled={busy} onClick={() => { clearDraft(); save.reset(); remove.reset(); }}>Cancel</button></div>
      </form>}
    </>}
  </section>;
}

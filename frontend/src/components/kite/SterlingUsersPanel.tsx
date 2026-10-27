import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useIsSimulation } from '../../store/useViewStore';
export type User = { id: string; username: string; role: 'admin' | 'user'; active: boolean; password_set: boolean; primary: boolean; photo?: string };
export type Draft = { id?: string; username: string; password: string; repeat: string; role: 'admin' | 'user'; active: boolean; primary?: boolean };

export type List = { current_user_id: string; users: User[] };

const BASE = '/api/v1/auth/users';
const KEY  = ['sterling-users'];

/** Gravatar URL from any string (username used as seed — no real email needed). */
function gravatarUrl(seed: string, size = 72): string {
  // Use a simple hash of the seed to generate a consistent colour avatar via gravatar's identicon
  let hash = 0;
  for (let i = 0; i < seed.length; i++) { hash = (Math.imul(31, hash) + seed.charCodeAt(i)) | 0; }
  const hex = Math.abs(hash).toString(16).padStart(32, '0').slice(0, 32);
  return `https://www.gravatar.com/avatar/${hex}?s=${size}&d=identicon&r=pg`;
}

function EyeIcon() {
  return (
    <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>
    </svg>
  );
}
function EyeOffIcon() {
  return (
    <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>
      <line x1="3" y1="3" x2="21" y2="21"/>
    </svg>
  );
}
function ShieldIcon() {
  return (
    <svg aria-hidden width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2 3 7v5c0 5.25 3.75 10.15 9 11.35C17.25 22.15 21 17.25 21 12V7l-9-5Z"/>
    </svg>
  );
}

/** Single expandable user card — same pattern as AccountCard */
function UserCard({ user, currentId, busy, onEdit, onDelete }: {
  user: User; currentId: string; busy: boolean;
  onEdit: (u: User) => void; onDelete: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [imgError, setImgError]  = useState(false);
  const isYou    = user.id === currentId;
  const isPrimary = user.primary;
  // Priority: saved photo → Gravatar identicon
  const avatarUrl = (user.photo && !imgError) ? user.photo : gravatarUrl(user.username);

  return (
    <div className="bac-card">
      {/* ── Header ── */}
      <div
        className="bac-header"
        role="button" tabIndex={0} aria-expanded={expanded}
        aria-label={`Manage ${user.username}`}
        onClick={() => setExpanded(v => !v)}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(v => !v); } }}
      >
        {/* Avatar — Gravatar with initials fallback */}
        <div className="sup-avatar" aria-hidden>
          {!imgError
            ? <img src={avatarUrl} width={36} height={36} alt="" onError={() => setImgError(true)} />
            : user.username.slice(0, 2).toUpperCase()
          }
        </div>

        <div className="bac-header-text">
          <div className="bac-header-name">
            {user.username}
            {user.role === 'admin' && (
              <span className="bac-badge-active sup-badge-admin"><ShieldIcon /> Admin</span>
            )}
            {isYou && <span className="sup-badge-you">You</span>}
          </div>
          <div className="bac-header-meta">
            <span className={`bac-status-pill${user.active ? ' is-on' : ' is-off'}`}>
              {user.active ? 'Active' : 'Suspended'}
            </span>
            {user.primary && <span className="bac-validity">Primary</span>}
            {!user.password_set && <span className="bac-validity">No password set</span>}
          </div>
        </div>

        <svg className="bac-chevron" style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}
          aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="m6 9 6 6 6-6"/>
        </svg>
      </div>

      {/* ── Expanded body ── */}
      {expanded && (
        <div className="bac-body">
          {/* Profile picture */}
          <div className="bac-section">
            <div className="sup-avatar-row">
              <div className="sup-avatar-lg" aria-hidden>
                <img
                  src={(user.photo && !imgError) ? user.photo : gravatarUrl(user.username, 96)}
                  width={56} height={56} alt=""
                  onError={() => setImgError(true)}
                />
              </div>
            </div>
          </div>

          {/* Details */}
          <div className="bac-section">
            <div className="bac-sl">Details</div>
            <div className="sup-detail-grid">
              <span className="bac-key-label">Username</span><span>{user.username}</span>
              <span className="bac-key-label">Role</span>
              <span>{user.role === 'admin' ? 'Administrator' : 'User'}</span>
              <span className="bac-key-label">Status</span>
              <span className={user.active ? 'bac-ok' : 'bac-err'}>{user.active ? '● Connected' : '○ Offline'}</span>
            </div>
          </div>

          {/* Footer actions */}
          <div className="bac-footer">
            <button className="bac-btn bac-btn-primary" disabled={busy} onClick={() => onEdit(user)}>
              Edit user
            </button>
            {user.active && !isPrimary && !isYou && (
              <button
                className="bac-btn bac-btn-danger bac-btn-ghost"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(`Suspend ${user.username}'s login? Sessions and feature access will be revoked. Broker connections and position protection are retained.`))
                    onDelete(user.id);
                }}
              >
                Suspend login
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function SterlingUsersPanel({ standalone = false }: { standalone?: boolean } = {}) {
  const selectedSimulation = useIsSimulation();
  const simulation = selectedSimulation && !standalone;
  const qc         = useQueryClient();
  const query      = useQuery({ queryKey: KEY, queryFn: () => api.get<List>(BASE), enabled: !simulation });

  const [draft,   setDraft]   = useState<Draft | null>(null);
  const [visible, setVisible] = useState(false);
  const [error,   setError]   = useState('');
  const [notice,  setNotice]  = useState('');

  const save = useMutation({
    mutationFn: (v: Draft) => {
      const { id, repeat: _r, ...body } = v;
      return id ? api.put<User>(`${BASE}/${encodeURIComponent(id)}`, body)
                : api.post<User>(BASE, body);
    },
    onSuccess: (_result, v) => {
      setDraft(null); setVisible(false);
      qc.invalidateQueries({ queryKey: KEY });
      setNotice(v.id === query.data?.current_user_id
        ? 'Sterling login updated. Sign in again with your updated credentials.'
        : 'User saved. Existing sessions for an edited user are signed out.');
    },
    onError: (e: Error) => {
      setError(e.message); setVisible(false);
      setDraft(prev => prev ? { ...prev, password: '', repeat: '' } : null);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`${BASE}/${encodeURIComponent(id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      setNotice('Login suspended. Enable the login to restore the account, then explicitly review and re-enable its access.');
    },
    onError: (e: Error) => setError(e.message),
  });

  const openEdit = (user?: User) => {
    setError(''); setNotice(''); setVisible(false);
    setDraft({ id: user?.id, username: user?.username || '', password: '', repeat: '',
               role: user?.role || 'user', active: user?.active ?? true });
  };

  const patch = (v: Partial<Draft>) => setDraft(prev => prev ? { ...prev, ...v } : null);
  const busy  = save.isPending || remove.isPending;
  const locked = draft?.id === query.data?.current_user_id
              || query.data?.users.some(u => u.id === draft?.id && u.primary);

  if (simulation) return <p>User management is available in Live view.</p>;
  if ((query.error as (Error & { status?: number }) | null)?.status === 403)
    return <p>Sterling user administration is available to administrators. Your linked broker accounts are managed below.</p>;

  return (
    <section className="settings-account-users account-users" style={{ padding: 0, color: 'var(--k-text)' }}>

      {/* ── Section header ── */}
      <div className="sup-heading">
        <div>
          <h2 style={{ margin: 0, fontSize: 14, fontWeight: 750 }}>Sterling users</h2>
          <span className="bac-muted" style={{ fontSize: 11 }}>Workspace access</span>
        </div>
        {query.data && (
          <button className="bac-btn bac-btn-primary" disabled={busy} onClick={() => openEdit()}>
            + Add user
          </button>
        )}
      </div>

      {/* ── Status messages ── */}
      {query.isLoading && <p className="bac-muted" role="status" style={{ padding: '12px 0' }}>Loading users…</p>}
      {query.isError   && <p className="bac-err"   role="alert">{(query.error as Error).message}</p>}
      {error           && <p className="bac-err"   role="alert"  style={{ marginBottom: 8 }}>{error}</p>}
      {notice          && <p className="bac-ok"    role="status" style={{ marginBottom: 8 }}>{notice}</p>}

      {/* ── User cards ── */}
      {query.data && (
        <div style={{ marginTop: 10 }}>
          {query.data.users.map(user => (
            <UserCard
              key={user.id}
              user={user}
              currentId={query.data.current_user_id}
              busy={busy}
              onEdit={openEdit}
              onDelete={id => remove.mutate(id)}
            />
          ))}
        </div>
      )}

      {/* ── Add / Edit form (inline modal card) ── */}
      {draft && (
        <div className="sup-form-card">
          <div className="sup-form-title">
            {draft.id ? 'Update user' : 'Add new user'}
          </div>
          <form
            className="bac-form"
            onSubmit={e => {
              e.preventDefault(); setError('');
              if (draft.password !== draft.repeat) { setError('Passwords do not match.'); return; }
              if ((!draft.id || draft.password) && draft.password.length < 12) { setError('Use at least 12 characters for the password.'); return; }
              if (draft.id === query.data?.current_user_id &&
                  !window.confirm('Update your Sterling login? Existing app sessions will be signed out. Your broker session stays connected.')) return;
              save.mutate(draft);
            }}
          >
            <label className="bac-label">Username
              <input className="bac-input" required minLength={3} maxLength={64}
                value={draft.username} autoComplete="username"
                onChange={e => patch({ username: e.target.value })} />
            </label>

            <p className="bac-muted">
              {draft.role === 'admin' ? 'Designated Sterling administrator' : 'Personal Sterling account'}
            </p>

            <label className="bac-label">
              {draft.id ? 'New password (blank keeps existing)' : 'Password'}
              <div className="bac-input-row">
                <input className="bac-input" type={visible ? 'text' : 'password'}
                  required={!draft.id} maxLength={256}
                  value={draft.password} autoComplete="new-password"
                  onChange={e => patch({ password: e.target.value })} />
                <button type="button" className="bac-btn bac-btn-icon"
                  aria-label={visible ? 'Hide password' : 'Show password'}
                  onClick={() => setVisible(v => !v)}>
                  {visible ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
            </label>

            <label className="bac-label">Repeat password
              <input className="bac-input" type="password" maxLength={256}
                value={draft.repeat} autoComplete="new-password"
                onChange={e => patch({ repeat: e.target.value })} />
            </label>

            <label className="bac-label sup-checkbox-label">
              <input type="checkbox" checked={draft.active} disabled={!!locked}
                onChange={e => patch({ active: e.target.checked })} />
              Login enabled
            </label>

            {save.error && <p className="bac-err">✗ {(save.error as Error).message}</p>}

            <div className="bac-row-gap">
              <button type="submit" className="bac-btn bac-btn-primary" disabled={busy}>
                {save.isPending ? 'Saving…' : 'Save user'}
              </button>
              <button type="button" className="bac-btn" disabled={busy}
                onClick={() => { setDraft(null); setVisible(false); }}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

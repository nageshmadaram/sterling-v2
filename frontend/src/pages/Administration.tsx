import { CentralConnections } from '../components/admin/CentralConnections';
import { CentralIntegrations } from '../components/admin/CentralIntegrations';
import { AccessRequests, type AccessRequest } from '../components/admin/AccessRequests';
import { UserCombobox } from '../components/admin/UserCombobox';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SterlingUsersPanel, type List } from '../components/kite/SterlingUsersPanel';
import { AccessEditor } from '../components/admin/AccessEditor';
import { useConfigurationCapabilities } from '../hooks/useConfigurationCapabilities';
import { api } from '../utils/api';
import '../styles/administration.css';
export default function Administration() {
  const capabilities = useConfigurationCapabilities();
  const allowed = capabilities.data?.active && capabilities.data?.administrator && !capabilities.isError;
  const users = useQuery({ queryKey: ['sterling-users'], queryFn: () => api.get<List>('/api/v1/auth/users'), enabled: !!allowed });
  const inbox = useQuery({ queryKey: ['admin-access-requests'], queryFn: () => api.get<{ requests: AccessRequest[] }>('/api/v1/admin/access-requests'), enabled: !!allowed, refetchInterval: 10_000 });
  const [tab, setTab] = useState<'users' | 'requests' | 'brokers' | 'integrations'>('users');
  const [selected, setSelected] = useState('');
  const pending = inbox.data?.requests.filter(request => request.status === 'pending').length || 0;
  if (capabilities.isLoading) return <main className="administration" role="status">Checking administration access…</main>;
  if (!allowed) return <main className="administration"><h1>Administration</h1><p role="alert">{capabilities.error?.message || 'This workspace requires the designated administrator.'}</p></main>;
  return <main className="administration">
    <header><div><div className="admin-brand">STERLING / MANAGEMENT</div><h1>Administration</h1><p>A clear view of your people, permissions and connections.</p></div><span className="admin-role-badge">Administrator</span></header>
    <div className="admin-summary"><div><strong>{users.data?.users.length ?? '—'}</strong><span>Sterling users</span></div><button type="button" onClick={() => setTab('requests')}><strong>{pending}</strong><span>Pending requests</span></button><div><strong>{users.data?.users.filter(user => user.active).length ?? '—'}</strong><span>Active accounts</span></div></div>
    {pending > 0 && tab !== 'requests' && <button className="admin-inbox-banner" onClick={() => setTab('requests')}><span><strong>{pending} access {pending === 1 ? 'request needs' : 'requests need'} your review</strong><small>Approve or reject workspace access for your users.</small></span><span>Review requests →</span></button>}
    {inbox.error && <p role="alert">Access requests could not be loaded. {inbox.error.message}</p>}
    <nav className="admin-tabs" aria-label="Administration sections">{(['users', 'requests', 'brokers', 'integrations'] as const).map(value =>
      <button key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => setTab(value)}>{value === 'users' ? 'Users & access' : value === 'requests' ? `Access requests${pending ? ` · ${pending}` : ''}` : value === 'brokers' ? 'Broker connections' : 'Telegram integrations'}</button>)}</nav>
    {tab === 'requests' ? <AccessRequests requests={inbox.data?.requests || []} /> : tab === 'brokers' ? <CentralConnections users={users.data?.users || []} /> : tab === 'integrations' ? <CentralIntegrations users={users.data?.users || []} /> :
      <div className="admin-layout"><section className="admin-people"><SterlingUsersPanel standalone /></section><section className="admin-policy" aria-label="Account permissions"><div className="admin-section-title"><h2>User permissions</h2><p>Select a Sterling user to manage their workspaces and settings.</p></div>
        <UserCombobox users={users.data?.users || []} value={selected} onChange={setSelected} label="Manage user access" />
        {users.isError && <p role="alert">{users.error.message}</p>}{selected ? <AccessEditor key={selected} userId={selected} /> : <div className="admin-empty"><span aria-hidden>◎</span><p>Search by username above to view and manage access.</p></div>}
      </section></div>}
  </main>;
}

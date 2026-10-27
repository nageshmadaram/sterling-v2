import { CentralConnections } from '../components/admin/CentralConnections';
import { CentralIntegrations } from '../components/admin/CentralIntegrations';
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
  const [tab, setTab] = useState<'users' | 'brokers' | 'integrations'>('users');
  const [selected, setSelected] = useState('');
  const [search, setSearch] = useState('');
  if (capabilities.isLoading) return <main className="administration" role="status">Checking administration access…</main>;
  if (!allowed) return <main className="administration"><h1>Administration</h1><p role="alert">{capabilities.error?.message || 'This workspace requires the designated administrator.'}</p><a href="/">Return to Sterling</a></main>;
  return <main className="administration">
    <header><div><div className="admin-brand">STERLING / ADMINISTRATION</div><h1>Account workspace</h1><p>People, feature access and delegated settings.</p></div><a className="bac-btn" href="/">Return to Sterling</a></header>
    <nav className="admin-tabs" aria-label="Administration sections">{(['users', 'brokers', 'integrations'] as const).map(value =>
      <button key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => setTab(value)}>{value === 'users' ? 'Users & access' : value === 'brokers' ? 'Broker connections' : 'Telegram integrations'}</button>)}</nav>
    {tab === 'brokers' ? <CentralConnections users={users.data?.users || []} /> : tab === 'integrations' ? <CentralIntegrations users={users.data?.users || []} /> :
    <div className="admin-layout"><section className="admin-people"><SterlingUsersPanel standalone /></section>
      <section className="admin-policy" aria-label="Account permissions"><h2>Access controls</h2>
        <label>Find an account<input className="bac-input" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search username" /></label>
        <label>Account<select aria-label="Account" className="bac-input" value={selected} onChange={e => setSelected(e.target.value)}>
          <option value="">Select an account</option>{users.data?.users.filter(user => user.username.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(user =>
            <option key={user.id} value={user.id}>{user.username}{user.active ? '' : ' (suspended)'}</option>)}
        </select></label>
        {users.isError && <p role="alert">{users.error.message}</p>}
        {selected ? <AccessEditor key={selected} userId={selected} /> : <p>Select an account to review its feature grants and settings visibility.</p>}
      </section></div>}
  </main>;
}

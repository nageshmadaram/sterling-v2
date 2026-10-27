import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { clearAuthTokens } from '../utils/auth';
import { notifyAuthExpired } from '../utils/liveSync';
import { PreferenceGate } from '../components/auth/PreferenceGate';
import { ThemeToggle } from '../components/kite/ThemeToggle';
import '../styles/workspace.css';
import { hasUnsavedDraft } from '../components/kite/config/unsavedDraftGuard';

const KiteRuntime = lazy(() => import('./KiteRuntime'));
const MediaWorkspace = lazy(() => import('./MediaWorkspace'));
const Administration = lazy(() => import('./Administration'));
export type Feature = 'kite' | 'tv' | 'radio';
type Destination = Feature | 'home' | 'administration';
export type SessionPolicy = { user_id: string; username: string; role: string; administrator: boolean;
  features: Record<Feature, boolean>; policy_revision: number };
const FEATURES: Feature[] = ['kite', 'radio', 'tv'];
const names = { kite: 'Kite', tv: 'Space', radio: 'Echo' };
const details = { kite: 'Your markets, positions and trading workspace.', tv: 'Live channels and your saved viewing library.', radio: 'Stations, music and listening collections.' };
function readDestination(): Destination {
  const path = window.location.pathname;
  return path === '/administration' ? 'administration' : path === '/kite' ? 'kite' : path === '/tv' ? 'tv' : path === '/radio' ? 'radio' : 'home';
}
export function WorkspaceRouter() {
  const qc = useQueryClient();
  const [destination, setDestination] = useState<Destination>(readDestination);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const policy = useQuery({ queryKey: ['sterling-session-policy'], queryFn: () => api.get<SessionPolicy>('/api/v1/auth/me'),
    staleTime: 0, refetchInterval: 10_000, refetchOnWindowFocus: true, retry: false });
  const navigate = useCallback((next: Destination) => {
    if (!['home', 'administration', ...FEATURES].includes(next)) return;
    if (next !== destination && hasUnsavedDraft() && !window.confirm('You have unsaved settings changes. Switch workspace and discard them?')) return;
    const url = new URL(window.location.href);
    url.pathname = next === 'home' ? '/' : '/' + next;
    window.history.pushState({}, '', url);
    setDestination(next);
    window.scrollTo(0, 0);
  }, [destination]);
  useEffect(() => {
    const pop = () => setDestination(readDestination());
    const change = (event: Event) => navigate((event as CustomEvent).detail);
    window.addEventListener('popstate', pop); window.addEventListener('sterling-navigate', change);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('sterling-navigate', change); };
  }, [navigate]);
  const session = policy.isError ? undefined : policy.data;
  const enabled = FEATURES.filter(feature => session?.features?.[feature] === true);
  const allowed = destination === 'home' || (destination === 'administration' ? session?.administrator === true : enabled.includes(destination));
  async function logout() {
    setSigningOut(true); setLogoutError('');
    try { await api.post('/api/v1/auth/logout', {}); clearAuthTokens(); qc.clear(); notifyAuthExpired(); }
    catch (error) { setLogoutError((error as Error).message); }
    finally { setSigningOut(false); }
  }
  return <div className={`workspace-shell ${destination === 'home' ? 'is-launcher' : ''}`}>
    <header className="workspace-header"><button className="workspace-wordmark" onClick={() => navigate('home')} aria-label="Sterling home">STERLING<span>STUDIO</span></button>
      <nav aria-label="Workspace navigation">
        {destination !== 'home' && <button onClick={() => navigate('home')}>Home</button>}
        {destination !== 'home' && enabled.map(feature => <button key={feature} aria-current={destination === feature ? 'page' : undefined} onClick={() => navigate(feature)}>{names[feature]}</button>)}
        {session?.administrator && <button aria-current={destination === 'administration' ? 'page' : undefined} onClick={() => navigate('administration')}>Administration</button>}
      </nav><div className="workspace-personal">{session && <details className="workspace-settings" key={destination}><summary>Settings</summary><div><button type="button" onClick={event => { event.currentTarget.closest('details')?.removeAttribute('open'); navigate('home'); }}>Switch workspace</button><p>Choose Kite, Echo or Space.</p></div></details>}<ThemeToggle /><button disabled={signingOut} onClick={() => void logout()}>{signingOut ? 'Signing out…' : 'Sign out'}</button></div>
    </header>
    {logoutError && <p role="alert" className="workspace-error">{logoutError}</p>}
    {policy.isLoading ? <main className="workspace-state" role="status">Opening your workspace…</main> : !session ?
      <main className="workspace-state"><h1>Access could not be verified</h1><p role="alert">{policy.error?.message || 'Try the connection again.'}</p><button onClick={() => void policy.refetch()}>Retry</button></main> : !allowed ?
      <main className="workspace-state"><h1>Access unavailable</h1><p>Your administrator has not enabled this destination.</p><button onClick={() => navigate('home')}>Choose a workspace</button></main> : destination === 'home' ?
      <main className="workspace-launcher"><div className="launcher-intro"><span className="workspace-eyebrow">YOUR PERSONAL WORKSPACE</span><h1>Welcome, {session.username}.</h1><p>Choose your workspace.</p></div>
        {enabled.length ? <div className="workspace-cards">{enabled.map((feature, index) => <button key={feature} className={`workspace-card card-${feature}`} onClick={() => navigate(feature)}>
          <span className="card-number">0{index + 1}</span><span className="card-glyph" aria-hidden>{feature === 'kite' ? '↗' : feature === 'tv' ? '▣' : '◉'}</span><h2>{names[feature]}</h2><p>{details[feature]}</p><span className="card-open">Open {names[feature]} <span aria-hidden>↗</span></span>
        </button>)}</div> : <section className="workspace-empty"><h2>No features enabled</h2><p>Your account is ready. Ask your administrator to enable Kite, Echo or Space.</p></section>}
        {new URLSearchParams(window.location.search).get('broker_login') === '1' && enabled.includes('kite') && <p className="workspace-pending">A broker connection is waiting. Choose Kite to continue.</p>}
        {session.administrator && <button className="launcher-admin" onClick={() => navigate('administration')}><span><strong>Administration</strong><small>Manage people, access and connection settings.</small></span><span aria-hidden>↗</span></button>}
        <footer className="launcher-footer">Your space. Your choice.</footer>
      </main> : <Suspense fallback={<main className="workspace-state" role="status">Loading {destination === 'administration' ? 'Administration' : names[destination]}…</main>}>
        {destination === 'administration' ? <Administration /> : destination === 'kite' ? <PreferenceGate><KiteRuntime /></PreferenceGate> : <PreferenceGate><MediaWorkspace key={destination} feature={destination} /></PreferenceGate>}
      </Suspense>}
  </div>;
}

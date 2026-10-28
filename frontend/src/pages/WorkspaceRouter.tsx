import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import { PreferenceGate } from '../components/auth/PreferenceGate';
import { AccountMenu } from '../components/AccountMenu';
import { ProfileMenu } from '../components/kite/ProfileMenu';
import { hasUnsavedDraft } from '../components/kite/config/unsavedDraftGuard';
import { SterlingLogo } from '../components/SterlingLogo';
import { WorkspaceChooser, WorkspaceDialog, WORKSPACE_NAMES, type Workspace } from '../components/WorkspaceChooser';
import { WorkspaceAccessRequest } from '../components/WorkspaceAccessRequest';
import '../styles/workspace.css';
const KiteRuntime = lazy(() => import('./KiteRuntime'));
const MediaWorkspace = lazy(() => import('./MediaWorkspace'));
const Administration = lazy(() => import('./Administration'));
export type Feature = Workspace;
type Destination = Feature | 'home' | 'administration';
export type SessionPolicy = { user_id: string; username: string; role: string; administrator: boolean; features: Record<Feature, boolean>; policy_revision: number };
const FEATURES: Feature[] = ['kite', 'radio', 'tv'];
function readDestination(): Destination { const path = window.location.pathname; return path === '/administration' ? 'administration' : path === '/kite' ? 'kite' : path === '/tv' ? 'tv' : path === '/radio' ? 'radio' : 'home'; }
export function WorkspaceRouter() {
  const [destination, setDestination] = useState<Destination>(readDestination), [chooserOpen, setChooserOpen] = useState(false);
  const policy = useQuery({ queryKey: ['sterling-session-policy'], queryFn: () => api.get<SessionPolicy>('/api/v1/auth/me'), staleTime: 0, refetchInterval: 10_000, refetchOnWindowFocus: true, retry: false });
  const session = policy.isError ? undefined : policy.data;
  // Administrators land in management; feature grants never determine management authority.
  const current = destination === 'home' && session?.administrator ? 'administration' : destination;
  const navigate = useCallback((next: Destination) => {
    if (!['home', 'administration', ...FEATURES].includes(next)) return;
    if (next !== destination && hasUnsavedDraft() && !window.confirm('You have unsaved settings changes. Switch workspace and discard them?')) return;
    window.dispatchEvent(new Event('sterling-home'));
    const url = new URL(window.location.href); url.pathname = next === 'home' ? '/' : '/' + next;
    window.history.pushState({}, '', url); setDestination(next); setChooserOpen(false); window.scrollTo(0, 0);
  }, [destination]);
  useEffect(() => {
    const pop = () => setDestination(readDestination());
    const open = () => { window.dispatchEvent(new Event('sterling-home')); setChooserOpen(true); };
    const change = (event: Event) => { const next = (event as CustomEvent).detail; if (next === 'home') open(); else navigate(next); };
    window.addEventListener('popstate', pop); window.addEventListener('sterling-navigate', change); window.addEventListener('sterling-select-workspace', open);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('sterling-navigate', change); window.removeEventListener('sterling-select-workspace', open); };
  }, [navigate]);

  // Cycle only explicitly granted workspaces; never bypass a per-user grant.
  const cycleWorkspace = useCallback(() => {
    const enabled = FEATURES.filter(feature => session?.features[feature]);
    if (!enabled.length) { setChooserOpen(true); return; }
    const index = enabled.indexOf(current as Feature);
    navigate(enabled[(index + 1) % enabled.length]);
  }, [current, navigate, session]);

  const chooser = session && <WorkspaceChooser features={session.features} administrator={session.administrator} onSelect={navigate} onAdmin={() => navigate('administration')} />;
  const allowed = current === 'home' || (current === 'administration' ? session?.administrator : session?.features[current]);
  return <div className={`workspace-shell ${current === 'kite' ? 'is-kite' : ''}`}>
    {(current !== 'kite' || !allowed) && <header className="workspace-header"><button type="button" className="workspace-wordmark" aria-label="Choose workspace" onClick={() => setChooserOpen(true)}><SterlingLogo size={26} /><span>STERLING</span></button>
      {(current==='tv'||current==='radio') ? <button type="button" className="workspace-current workspace-cycle"
        onClick={cycleWorkspace} title="Switch to the next permitted workspace"
        aria-label={`Switch workspace from ${WORKSPACE_NAMES[current]}`}>
        {WORKSPACE_NAMES[current]} <span aria-hidden="true">⌄</span>
      </button> : <span className="workspace-current">{current === 'administration' ? 'Administration' : current === 'home' ? '' : WORKSPACE_NAMES[current]}</span>}
      <div id="workspace-toolbar" className="workspace-personal" /><AccountMenu mediaKind={current==='tv'||current==='radio'?current:undefined}/></header>}
    {policy.isLoading ? <main className="workspace-state" role="status">Opening your workspace…</main> : !session ? <main className="workspace-state"><h1>Access could not be verified</h1><p role="alert">{policy.error?.message || 'Try the connection again.'}</p><button onClick={() => void policy.refetch()}>Retry</button></main> :
      !allowed && FEATURES.includes(current as Feature) ? <WorkspaceAccessRequest key={current} feature={current as Feature} onBack={() => navigate('home')} /> : !allowed ? <main className="workspace-state"><h1>Administrator access required</h1><button onClick={() => navigate('home')}>Return to your workspaces</button></main> :
      current === 'home' ? <main className="workspace-launcher">{chooser}</main> : <Suspense fallback={<main className="workspace-state" role="status">Opening workspace…</main>}>
        {current === 'administration' ? <Administration /> : current === 'kite' ? <PreferenceGate><KiteRuntime /></PreferenceGate> : <PreferenceGate><MediaWorkspace key={current} feature={current} /></PreferenceGate>}
      </Suspense>}
    {/* Account editing has its own authenticated API. Preserve a crop/draft
        across a failed workspace-policy poll; protected workspaces above
        still fail closed until the current policy can be verified. */}
    {policy.data && <ProfileMenu hiddenTrigger />}
    <WorkspaceDialog open={chooserOpen} onClose={() => setChooserOpen(false)}>{chooser}</WorkspaceDialog>
  </div>;
}

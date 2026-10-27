import { SessionRestoreCard } from './SessionRestoreCard';
import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { clearAuthTokens, loginSterling, rememberSterlingSession } from '../../utils/auth';
import '../../styles/workspace.css';
import { AUTH_EXPIRED_EVENT } from '../../utils/liveSync';

type Phase = 'checking' | 'login' | 'ready';

export function LoginGate({ children }: { children: ReactNode }) {
  const explicitSignIn = useRef(new URLSearchParams(window.location.search).get('sterling_login') === '1');
  const [phase, setPhase] = useState<Phase>(() => explicitSignIn.current ? 'login' : 'checking');
  const [connectionAttempt,setConnectionAttempt]=useState(0);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const qc = useQueryClient();
  const phaseRef = useRef<Phase>(phase);
  phaseRef.current = phase;

  // The session ended mid-use (refresh token rejected or revoked). Every poll
  // would keep failing with 401 while the screen kept painting its last data as
  // if it were live, so leave the dashboard and drop what it was showing.
  useEffect(() => {
    const onExpired = () => {
      if (phaseRef.current !== 'ready') return;
      clearAuthTokens();
      qc.clear();
      setPassword('');
      setError('Your session expired. Sign in again to resume live data.');
      setPhase('login');
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, [qc]);

  useEffect(() => {
    if (explicitSignIn.current) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const check = () => api.get('/api/v1/auth/me')
      .then(() => { if (alive) { setError(''); setPhase('ready'); } })
      .catch((err: Error & { status?: number }) => {
        if (!alive) return;
        if (err.status === 401) { clearAuthTokens(); setError(''); setPhase('login'); }
        else { setError('Sterling is reconnecting. Your saved settings and session are retained.'); retry=setTimeout(check,3000); }
      });
    void check();
    return () => { alive = false; clearTimeout(retry); };
  }, [connectionAttempt]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      if (remember) await loginSterling(username.trim(), password, true);
      else await loginSterling(username.trim(), password);
      qc.clear();
      await api.get('/api/v1/auth/me');
      explicitSignIn.current = false;
      const url = new URL(window.location.href);
      url.searchParams.delete('sterling_login');
      url.pathname = '/';
      window.history.replaceState(window.history.state, '', url.href);
      setPassword('');
      if (remember) await rememberSterlingSession();
      setPhase('ready');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setSubmitting(false);
    }
  }

  // Do not mount the workspace until the operator session is verified.
  // An inert wrapper disables interaction but does not stop subscriptions,
  // WebSockets, expensive charts or competing API calls during backend recovery.
  // After a successful sign-in ordinary temporary disconnections do not tear
  // down the authenticated workspace.
  if (phase === 'checking') return <SessionRestoreCard stage="session" message={error || 'Verifying Sterling session…'}>
    {error && <button type="button" className="sterling-retry-button" onClick={() => setConnectionAttempt(a => a + 1)}>Retry connection</button>}
  </SessionRestoreCard>;
  if (phase === 'ready') return <>{children}</>;

  return <main className="sterling-signin">
    <section className="signin-intro"><div className="signin-brand">STERLING</div></section>
    <form onSubmit={submit} className="signin-card" aria-label="Sterling login">
      <h1>Sign in</h1>
      <label htmlFor="sterling-username">Username</label>
      <input id="sterling-username" required autoFocus autoComplete="username" maxLength={64} value={username} onChange={e => setUsername(e.target.value)} />
      <label htmlFor="sterling-password">Password</label>
      <div className="signin-password"><input id="sterling-password" required type={passwordVisible ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
        <button type="button" aria-label={passwordVisible ? 'Hide password' : 'Show password'} onClick={() => setPasswordVisible(value => !value)}>{passwordVisible ? 'Hide' : 'Show'}</button></div>
      <label className="signin-remember"><input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />Remember this device</label>
      {error && <p role="alert" className="signin-error">{error}</p>}
      <button disabled={submitting || !username.trim() || !password} type="submit" className="signin-submit">{submitting ? 'Signing in…' : 'Sign in'}</button>
    </form>
  </main>;
}

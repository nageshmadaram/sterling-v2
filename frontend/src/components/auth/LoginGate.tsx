import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { clearAuthTokens, loginSterling } from '../../utils/auth';
import { AUTH_EXPIRED_EVENT } from '../../utils/liveSync';

type Phase = 'checking' | 'login' | 'ready';

export function LoginGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('checking');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
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
    let alive = true;
    api.get('/api/v1/auth/me')
      .then(() => { if (alive) setPhase('ready'); })
      .catch((err: Error & { status?: number }) => {
        if (!alive) return;
        if (err.status === 401) {
          clearAuthTokens();
          setPhase('login');
        } else {
          setError(`Sterling backend unavailable: ${err.message}`);
          setPhase('login');
        }
      });
    return () => { alive = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await loginSterling(username.trim(), password);
      await api.get('/api/v1/auth/me');
      setPassword('');
      setPhase('ready');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setSubmitting(false);
    }
  }

  if (phase !== 'login') {
    return <><div style={{display:'contents'}} inert={phase === 'checking'}>{children}</div>{phase === 'checking' && <div className="sterling-session-validation" role="status">Validating Sterling session…</div>}</>;
  }

  return (
    <div style={shell}>
      <form onSubmit={submit} style={card} aria-label="Sterling login">
        <div style={{ fontSize: 11, letterSpacing: 2, fontWeight: 800, opacity: 0.65 }}>STERLING</div>
        <h1 style={{ margin: '8px 0 4px', fontSize: 20 }}>Sterling sign in</h1>
        <p style={{ margin: '0 0 18px', fontSize: 12, opacity: 0.7 }}>
          Production trading controls require a verified Sterling identity.
        </p>
        <label style={label}>Username</label>
        <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} style={input} />
        <label style={label}>Password</label>
        <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} style={input} />
        {error && <div role="alert" style={{ color: '#d9534f', fontSize: 12, marginBottom: 12 }}>{error}</div>}
        <button disabled={submitting || !username.trim() || !password} type="submit" style={button}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

const shell = {
  minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24,
  background: 'var(--k-surface-sunken-2, #0f1115)', color: 'var(--k-text, #e6e8ee)',
} as const;
const card = {
  width: 'min(380px, 100%)', padding: 24, borderRadius: 10,
  border: '1px solid var(--k-border, #323947)', background: 'var(--k-bg, #13161c)',
} as const;
const label = { display: 'block', marginBottom: 5, fontSize: 11, fontWeight: 700 } as const;
const input = {
  width: '100%', boxSizing: 'border-box' as const, marginBottom: 14, padding: '10px 11px',
  borderRadius: 6, border: '1px solid var(--k-border, #323947)',
  background: 'transparent', color: 'inherit', font: 'inherit',
} as const;
const button = {
  width: '100%', padding: '10px 12px', border: 0, borderRadius: 6,
  background: 'var(--k-brand, #f06428)', color: 'var(--k-on-accent, white)',
  fontWeight: 800, cursor: 'pointer',
} as const;

import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';

/** Secrets are fetched only on explicit reveal and removed on hide/unmount. */
export function SavedKiteCredentials({ accountId, hasCredentials }: { accountId: string; hasCredentials: boolean }) {
  const [values, setValues] = useState<Partial<Record<'api_key' | 'api_secret', string>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setValues({});
    const hide = () => { generation.current += 1; setValues({}); };
    const timer = window.setInterval(hide, 30_000);
    window.addEventListener('blur', hide);
    return () => { generation.current += 1; clearInterval(timer); window.removeEventListener('blur', hide); };
  }, [accountId]);
  async function toggle(field: 'api_key' | 'api_secret') {
    if (values[field] !== undefined) {
      setValues(previous => { const next = { ...previous }; delete next[field]; return next; });
      return;
    }
    setBusy(field); setError('');
    const current = generation.current;
    try {
      const saved = await api.post<{ api_key: string; api_secret: string }>(`/api/v1/kite/accounts/${encodeURIComponent(accountId)}/credentials/reveal`, {});
      if (current === generation.current) setValues(previous => ({ ...previous, [field]: saved[field] }));
    } catch { if (current === generation.current) setError('Could not reveal saved credentials. Try again.'); }
    finally { setBusy(null); }
  }
  return <div>
    <div style={{ fontSize: 12, marginBottom: 6 }}>Currently saved credentials</div>
    {(['api_key', 'api_secret'] as const).map(field => {
      const label = field === 'api_key' ? 'API key' : 'API secret';
      const visible = values[field] !== undefined;
      return <div key={field} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
        <label style={{ flex: 1, fontSize: 12 }}>{label}
          <input aria-label={`Saved ${label}`} readOnly value={visible ? values[field] : hasCredentials ? '************' : ''}
            placeholder="Not configured" autoComplete="off" spellCheck={false}
            style={{ width: '100%', padding: 8, boxSizing: 'border-box', background: 'var(--k-bg)', color: 'var(--k-text)', border: '1px solid var(--k-border)', borderRadius: 7 }} />
        </label>
        <button type="button" aria-label={`${visible ? 'Hide' : 'Show'} saved ${label}`} aria-pressed={visible}
          disabled={!hasCredentials || busy !== null} onClick={() => void toggle(field)}
          style={{ cursor: 'pointer', padding: 8, background: 'var(--k-bg)', color: 'var(--k-text)', border: '1px solid var(--k-border)', borderRadius: 7 }}>
          <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />
            {visible && <path d="m3 3 18 18" />}
          </svg>
        </button>
      </div>;
    })}
    <div style={{ fontSize: 11, opacity: .7 }}>Eye reveals the saved value. Leave replacement fields blank to keep it. Revealed values hide automatically.</div>
    {error && <div role="alert">{error}</div>}
  </div>;
}

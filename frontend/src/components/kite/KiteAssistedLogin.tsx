import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useKiteAccounts } from '../../hooks/useKite';

type Attempt = { id: string; account_id: string; username: string; phase: string; message: string; credential_status?: string; error_code?: string; stage?: string; test_only?: boolean; verification_method?: string };
const BASE = '/api/v1/kite/assisted-login';
const steps = [
  ['opening', 'Opening Kite'],
  ['submitting_password', 'Submitting saved credentials'],
  ['awaiting_totp', 'Waiting for your TOTP'],
  ['verifying_totp', 'Verifying TOTP'],
  ['saving_session', 'Saving broker session'],
  ['done', 'Connected'],
];
const terminal = (phase: string) => ['done', 'tested', 'failed', 'cancelled'].includes(phase);
export function KiteAssistedLogin({ accountId, autoStart = false }: { accountId?: string; autoStart?: boolean }) {
  const accounts = useKiteAccounts();
  const qc = useQueryClient();
  const [selected, setSelected] = useState(accountId ?? '');
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<{ image: string; captured_at_ms: number } | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const submittingCode = useRef(false);
  const autoStarted = useRef(false);
  const [previewClock, setPreviewClock] = useState(Date.now());
  const running = !!attempt && !terminal(attempt.phase);
  useEffect(() => {
    if (!attempt || terminal(attempt.phase)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.get<Attempt>(`${BASE}/${encodeURIComponent(attempt.id)}`);
        if (!active) return;
        setAttempt(next); setError('');
        if (next.phase === 'done') {
          qc.invalidateQueries({ queryKey: ['kite-status'] });
          qc.invalidateQueries({ queryKey: ['kite-accounts'] });
        } else if (!terminal(next.phase)) timer = setTimeout(poll, 1000);
      } catch (e) {
        if (active) { setError((e as Error).message); timer = setTimeout(poll, 3000); }
      }
    };
    timer = setTimeout(poll, 500);
    return () => { active = false; clearTimeout(timer); };
  }, [attempt?.id, attempt?.phase === 'done', attempt?.phase === 'failed', attempt?.phase === 'cancelled', attempt?.phase === 'tested', qc]);
  const previewActive = showPreview && !!attempt && ['submitting_password', 'awaiting_totp', 'verifying_totp'].includes(attempt.phase);
  useEffect(() => {
    setPreview(null); setPreviewError('');
    if (!attempt || !previewActive) return;
    let active = true;
    const clock = setInterval(() => setPreviewClock(Date.now()), 1000);
    let timer: ReturnType<typeof setTimeout>;
    const frame = async () => {
      try {
        const next = await api.get<{ image: string; captured_at_ms: number }>(`${BASE}/${encodeURIComponent(attempt.id)}/preview`);
        if (active) {
          if (!next?.image?.startsWith('data:image/png;base64,')) throw new Error('Invalid preview');
          setPreview(next); setPreviewError('');
        }
      } catch {
        if (active) { setPreview(null); setPreviewError('Preview temporarily unavailable. Login progress is still shown below.'); }
      } finally {
        if (active) timer = setTimeout(frame, 1000);
      }
    };
    void frame();
    return () => { active = false; clearTimeout(timer); clearInterval(clock); };
  }, [attempt?.id, previewActive]);
  const start = async (testOnly = false) => {
    setBusy(true); setError(''); setCode(''); setPreview(null); setPreviewError(''); setShowPreview(false);
    try { setAttempt(await api.post<Attempt>(BASE, { account_id: selected, ...(testOnly ? { test_only: true } : {}) })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!autoStart || autoStarted.current || !selected || !accounts.data?.accounts.some(a => a.id === selected)) return;
    autoStarted.current = true;
    void start();
  }, [autoStart, selected, accounts.data]);
  const submitCode = async (digits: string) => {
    if (digits.length !== 6 || !attempt || attempt.phase !== 'awaiting_totp' || submittingCode.current) return;
    submittingCode.current = true; setBusy(true); setError(''); setCode('');
    try { setAttempt(await api.post<Attempt>(`${BASE}/${encodeURIComponent(attempt.id)}/totp`, { code: digits })); }
    catch (failure) { setError((failure as Error).message); }
    finally { submittingCode.current = false; setBusy(false); }
  };
  return <section aria-label="Assisted broker login" className="assisted-login">
    <div className="account-section-heading"><strong>Broker login</strong><span className="account-caption">Saved credentials</span></div>
    <label>Broker account<select aria-label="Broker account" disabled={running || busy} value={selected} onChange={e => { setSelected(e.target.value); setAttempt(null); setError(''); }}>
      <option value="">Choose a saved broker account</option>
      {accounts.data?.accounts.map(a => <option key={a.id} value={a.id}>{a.label || 'Kite'}{a.kite_user_id ? ` · ${a.kite_user_id}` : ''}</option>)}
    </select></label>
    {accounts.error && <p role="alert">{accounts.error.message}</p>}

    {!running && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <button type="button" className="account-primary" aria-label="Login with saved credentials" disabled={!selected || busy} onClick={() => start()}>{busy ? 'Starting…' : 'Log in'}</button>
      <button type="button" className="account-secondary" aria-label="Test saved credentials" disabled={!selected || busy} onClick={() => start(true)}>Test credentials</button>
    </div>}
    {attempt?.phase === 'awaiting_totp' && <form aria-label="Enter Kite verification code" style={{ padding: 14, border: '2px solid var(--k-border)', borderRadius: 8, display: 'grid', gap: 10 }} onSubmit={async e => {
      e.preventDefault(); void submitCode(code);
    }}>
      <strong>{attempt.verification_method === 'Kite Mobile App Code' ? 'Enter your Kite Mobile App Code' : 'Enter your TOTP'}</strong>
      <p style={{ margin: 0, fontSize: 12 }}>{attempt.verification_method === 'Kite Mobile App Code' ? 'Use the six-digit code shown in your Kite mobile app.' : 'Use the current six-digit code from your authenticator app.'}</p>
      <label>{attempt.verification_method || 'TOTP'}<input autoFocus aria-label={attempt.verification_method === 'Kite Mobile App Code' ? 'Kite Mobile App Code' : 'Kite TOTP'} type="password" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} disabled={busy} onChange={e => { const digits = e.target.value.replace(/\D/g, '').slice(0, 6); setCode(digits); if (digits.length === 6) void submitCode(digits); }} /></label>
      <span className="account-caption">Submits automatically after six digits.</span>
      <button type="submit" disabled={busy || code.length !== 6}>Verify {attempt.verification_method === 'Kite Mobile App Code' ? 'App Code' : 'TOTP'}</button>
    </form>}
    {attempt && <div className="assisted-progress" data-phase={attempt.phase} aria-label="Broker login progress" style={{ border: '1px solid var(--k-border)', borderRadius: 9, overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px', background: 'var(--k-surface-2)' }}>
        <strong>Kite login · {attempt.username}</strong>
        {!terminal(attempt.phase) && <button type="button" aria-expanded={showPreview} onClick={() => setShowPreview(previous => !previous)} style={{ marginTop: 6, cursor: 'pointer' }}>{showPreview ? 'Hide live browser view' : 'Show live browser view'}</button>}
        {showPreview && <div style={{ fontSize: 11, marginTop: 4 }}>Kite page · read only · sensitive fields hidden</div>}
      </div>
      {previewActive && <div style={{ padding: 8, background: '#f8fafc' }}>
        {preview ? <>
          <img src={preview.image} alt="Live Kite login page with credential fields hidden" style={{ display: 'block', width: '100%', maxHeight: 380, objectFit: 'contain', background: '#fff', borderRadius: 6 }} />
          <p style={{ color: '#475569', fontSize: 10, margin: '6px 0 0' }}><span className={previewClock - preview.captured_at_ms > 5000 ? 'preview-paused' : 'preview-heartbeat'}>{previewClock - preview.captured_at_ms > 5000 ? 'Update delayed' : 'Refreshing every second'}</span> · {new Date(preview.captured_at_ms).toLocaleTimeString()} · Password and OTP fields hidden</p>
        </> : <p style={{ color: '#475569', fontSize: 11, margin: 8 }}>{previewError || 'Connecting to the Kite page preview…'}</p>}
      </div>}
      <details className="account-recovery"><summary>Activity</summary>
      <ol aria-label="Login steps" style={{ padding: '8px 12px', margin: 0, listStyle: 'none', display: 'grid', gap: 7 }}>
        {steps.map(([phase, label], index) => {
          const failedPhase = ({ browser_start: 'opening', opening_page: 'opening', filling_credentials: 'submitting_password', waiting_totp: 'awaiting_totp', verification_input: 'verifying_totp', verification_submit: 'verifying_totp', verification_callback: 'verifying_totp' } as Record<string, string>)[attempt.stage || ''] || attempt.stage;
          const current = attempt.phase === 'tested' ? 2 : steps.findIndex(([name]) => name === (attempt.phase === 'failed' ? failedPhase : attempt.phase));
          const complete = current > index || attempt.phase === 'done';
          const active = attempt.phase === phase && !terminal(attempt.phase);
          return <li key={phase} aria-current={active ? 'step' : undefined} style={{ fontSize: 11, opacity: complete || active ? 1 : .55, display: 'flex', gap: 8 }}>
            <span aria-hidden="true">{complete ? '✓' : attempt.phase === 'failed' && current === index ? '!' : active ? '●' : '○'}</span>
            <span>{attempt.verification_method === 'Kite Mobile App Code' ? label.replace('TOTP', 'Mobile App Code') : label}{active ? '…' : ''}</span>
          </li>;
        })}
      </ol>
      </details>

      <p role="status" className={running && attempt.phase !== 'awaiting_totp' ? 'assisted-status-busy' : ''} style={{ padding: '0 12px 10px', margin: 0 }}>{attempt.message}</p>
    </div>}

    {running && <button type="button" disabled={busy} onClick={async () => {
      setBusy(true);
      try { await api.delete(`${BASE}/${encodeURIComponent(attempt!.id)}`); setAttempt(previous => previous ? { ...previous, phase: 'cancelled', message: 'Broker login cancelled.' } : null); setCode(''); }
      catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>Cancel broker login</button>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

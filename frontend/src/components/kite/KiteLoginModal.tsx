import React from 'react';
import { k, tint } from '../../styles/kiteUI';
import type { KiteLoginPhase } from '../../hooks/useKite';
import { KiteLoginLinkFallback } from './KiteLoginLinkFallback';

/**
 * The Kite login handshake, shown inside the app.
 *
 * The login itself has to happen on Zerodha's own page — their 2FA is
 * interactive by design and cannot be embedded (their headers forbid framing,
 * and so do ours). So this is not the login; it is the app saying what is
 * happening while a small popup does it, and saying it here rather than leaving
 * the operator staring at an unchanged screen.
 *
 * The account pane provides recovery for a callback that cannot load on a
 * phone. Operators can leave this waiting overlay to paste the complete failed
 * callback address there while the broker callback listener remains mounted.
 *
 * Rendered as a portal-free overlay on purpose: it is owned by the pane that
 * starts the login, so it lives and dies with it.
 */
export function KiteLoginModal({
  phase, error, onRetry, onDismiss, loginUrl, onContinue,
}: {
  phase: KiteLoginPhase;
  error: string | null;
  onRetry: () => void;
  onDismiss: () => void;
  loginUrl?: string | null;
  onContinue?: () => void;
}) {
  const done = phase === 'done';
  const failed = phase === 'failed';
  const canDismiss = phase !== 'opening' && phase !== 'idle';

  React.useEffect(() => {
    if (!canDismiss) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onDismiss(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [canDismiss, onDismiss]);

  if (phase === 'idle') return null;

  const tone = done ? k.green : failed ? k.red : k.blue;

  const title = done ? 'Connected to Kite'
    : failed ? 'Login did not finish'
    : phase === 'opening' ? 'Opening Kite…'
    : 'Waiting for Kite';

  const body = done
    ? 'Your Kite login succeeded. Sterling is connected to your broker account. You can continue using the desk.'
    : failed
      ? (error ?? 'The login did not complete.')
      : phase === 'opening'
        ? 'Fetching a fresh login link (valid about 15 minutes).'
        : 'Finish signing in on the Kite window. Use your usual browser’s password manager to autofill your Kite credentials, then enter TOTP on Kite. Sterling cannot see those steps. After Kite redirects back, Sterling saves the session automatically and this page confirms success. If your phone stops at an unreachable localhost page, select Finish mobile login, then paste that page’s full address into Connect.';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{
        position: 'fixed', inset: 0, zIndex: 10050,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,.34)', padding: 24,
      }}
      onClick={() => { if (canDismiss) onDismiss(); }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 380, background: k.bg,
          border: `1px solid ${k.border}`, borderRadius: 10, overflow: 'hidden',
          boxShadow: '0 1px 2px rgba(0,0,0,.04), 0 14px 36px rgba(0,0,0,.18)',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px',
          background: tint(tone, 10), borderBottom: `1px solid ${k.border}`,
        }}>
          <span
            aria-hidden
            style={{
              width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              background: tint(tone, 22), color: tone, fontSize: 13, fontWeight: 800,
            }}
          >
            {done ? '✓' : failed ? '!' : '·'}
          </span>
          <span style={{ fontSize: 12.5, fontWeight: 700, color: k.text }}>{title}</span>
        </div>

        <div style={{ padding: '14px 16px', fontSize: 11, color: k.dim, lineHeight: 1.6 }}>
          {body}
          {!done && loginUrl && onContinue && <KiteLoginLinkFallback url={loginUrl} onContinue={onContinue} />}
          {!done && !failed && (
            /* A determinate bar would be a lie: how long this takes is however
               long the operator spends on Zerodha's 2FA. */
            <div style={{
              marginTop: 12, height: 2, borderRadius: 2, overflow: 'hidden',
              background: k.border,
            }}>
              <div style={{
                width: '38%', height: '100%', background: tone,
                animation: 'kite-login-slide 1.15s ease-in-out infinite',
              }} />
            </div>
          )}
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8,
          padding: '11px 16px', borderTop: `1px solid ${k.border}`, background: k.surface,
        }}>
          {failed && (
            <button
              type="button"
              onClick={onRetry}
              style={{
                minHeight: 30, padding: '0 12px', borderRadius: 6, cursor: 'pointer',
                border: 'none', background: k.blue, color: 'var(--k-on-accent)',
                fontFamily: 'inherit', fontSize: 11, fontWeight: 700,
              }}
            >
              Try again
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            disabled={!canDismiss}
            title={canDismiss ? undefined : 'Wait for the fresh login link'}
            style={{
              minHeight: 30, padding: '0 12px', borderRadius: 6,
              border: `1px solid ${k.border}`, background: k.bg,
              color: canDismiss ? k.text : k.dim,
              fontFamily: 'inherit', fontSize: 11, fontWeight: 600,
              cursor: canDismiss ? 'pointer' : 'not-allowed',
              opacity: canDismiss ? 1 : 0.55,
            }}
          >
            {done ? 'Done' : phase === 'waiting' ? 'Finish mobile login' : 'Close'}
          </button>
        </div>
      </div>

      <style>{'@keyframes kite-login-slide{0%{transform:translateX(-100%)}100%{transform:translateX(320%)}}'}</style>
    </div>
  );
}

export default KiteLoginModal;

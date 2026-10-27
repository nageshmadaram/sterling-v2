import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  useKiteAccounts, useActivateKiteAccount, useKiteStatus, useKiteAuthBroadcast, useGenerateKiteSession, useOpenKiteLogin,
  useRefreshKiteSession, useKiteLogout,
} from '../../hooks/useKite';
import { notifyOrder } from '../../store/useKiteNotifications';
import { authSuccess, authIdle } from '../../store/useAuthFeedback';
import { useKiteStatusCardStore } from '../../store/useKiteStatusCardStore';
import { ensureMacLoadingStyles } from './MacLoadingSurface';
import { KiteLoader, ButtonLoader } from './KiteLoader';
import { k } from '../../styles/kiteUI';
import { EgressAddressRow } from './EgressAddressRow';
import { KiteAssistedLogin } from './KiteAssistedLogin';
import { KiteLoginLinkFallback } from './KiteLoginLinkFallback';

const DISMISS_KEY = 'sterling_kite_session_guard_dismissed';

export function KiteSessionGuard({ onOpenAccountSettings }: { onOpenAccountSettings?: () => void } = {}) {
  useKiteAuthBroadcast();
  const { data: status, isFetching = false, error: statusError, refetch } = useKiteStatus();
  const gen = useGenerateKiteSession();
  const refresh = useRefreshKiteSession();
  const logout = useKiteLogout();

  const loginRequested = useKiteStatusCardStore(state => state.loginRequested);
  const loginAccountId = useKiteStatusCardStore(state => state.loginAccountId);
  const ownedAccounts = useKiteAccounts();
  const activateAccount = useActivateKiteAccount();
  const effectiveAccountId = loginAccountId || status?.account_id || ownedAccounts.data?.active_id || ownedAccounts.data?.accounts[0]?.id || undefined;
  const kiteLogin = useOpenKiteLogin(effectiveAccountId);
  const isOpen = useKiteStatusCardStore((state) => state.isOpen);
  const openCard = useKiteStatusCardStore((state) => state.openCard);
  const closeCard = useKiteStatusCardStore((state) => state.closeCard);

  const [reqToken, setReqToken] = useState('');
  const [savedAccountId, setSavedAccountId] = useState<string | null>(null);
  const [verificationTimedOut, setVerificationTimedOut] = useState(false);
  const onSessionSaved = useCallback((id: string) => {
    setSavedAccountId(id);
    setVerificationTimedOut(false);
    void refetch();
  }, [refetch]);
  // An assisted login can finish without a callback-window broadcast.
  // Recheck the authoritative active-account status for a bounded window.
  useEffect(() => {
    if (!savedAccountId || (status?.connected && status.account_id === savedAccountId)) return;
    let checks = 0;
    const timer = window.setInterval(() => {
      if (++checks >= 6) { window.clearInterval(timer); setVerificationTimedOut(true); }
      void refetch();
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [savedAccountId, status?.connected, status?.account_id, refetch]);
  const [mounted, setMounted] = useState(isOpen);
  const [leaving, setLeaving] = useState(false);
  const prevConnected = useRef<boolean | null>(null);
  const notifiedRef = useRef(false);
  const graceTimer = useRef<number | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const hasAccount = !!effectiveAccountId;
  const unknown = !!status?.transient || !!statusError;
  const connected = !!status?.connected && !unknown
    && (!savedAccountId || status.account_id === savedAccountId);
  const checking = !status && isFetching;
  const checkMessage = statusError?.message || status?.message || 'Session verification is unavailable. Retry the check or open Kite Login.';
  const permissionDenied = unknown && /permission|not permitted|allowlist/i.test(checkMessage);
  const canAutoRecover = !!status?.has_refresh_token;

  ensureMacLoadingStyles();

  const handleDismiss = () => {
    try {
      sessionStorage.setItem(DISMISS_KEY, 'true');
    } catch {}
    if (graceTimer.current) {
      window.clearTimeout(graceTimer.current);
      graceTimer.current = null;
    }
    closeCard();
  };

  useEffect(() => {
    if (isOpen) {
      setMounted(true);
      setLeaving(false);
      return;
    }
    if (!mounted) return;
    setLeaving(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setLeaving(false);
    }, 160);
    return () => window.clearTimeout(timer);
  }, [isOpen, mounted]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) handleDismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen]);

  useEffect(() => {
    if (!status) return;

    const was = prevConnected.current;
    if (!unknown) prevConnected.current = connected;

    if (connected) {
      try {
        sessionStorage.removeItem(DISMISS_KEY);
      } catch {}
      notifiedRef.current = false;
      if (graceTimer.current) {
        window.clearTimeout(graceTimer.current);
        graceTimer.current = null;
      }
      if (was === false) {
        authSuccess(status?.user_name ? `Connected · ${status.user_name}` : 'Kite connected');
        notifyOrder({
          kind: 'complete',
          title: 'Kite connected',
          message: status?.user_name
            ? `Signed in as ${status.user_name}${status.kite_user_id ? ` (${status.kite_user_id})` : ''}.`
            : 'Your Kite session is now active.',
        });
      }
      return;
    }

    let isDismissed = false;
    try {
      isDismissed = sessionStorage.getItem(DISMISS_KEY) === 'true';
    } catch {}

    if (unknown) return;

    if (!connected && !isDismissed) {
      if (was === true && hasAccount) {
        if (notifiedRef.current) return;
        notifiedRef.current = true;

        notifyOrder({
          kind: 'error',
          title: 'Kite session expired',
          message: canAutoRecover
            ? 'Renewing automatically… reconnect manually if this persists.'
            : 'Your Kite session has lapsed. Reconnect to resume live data and trading.',
        });

      }

    } else if (was === true) {
      authIdle();
    }
  }, [status, connected, hasAccount, canAutoRecover, unknown, openCard]);

  useEffect(() => () => {
    if (graceTimer.current) window.clearTimeout(graceTimer.current);
  }, []);

  useEffect(() => {
    if (!isOpen || !mounted) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, [isOpen, mounted]);

  const [showRelogin, setShowRelogin] = useState(false);
  useEffect(() => { logout.reset?.(); }, [connected, status?.account_id]);

  if (!mounted || typeof document === 'undefined') return null;

  // The account list persists the successful login receipt across reloads,
  // while /status verifies the one ACTIVE broker account. Display a stored
  // token honestly without claiming the broker accepted it.
  const selectedStoredAccount = ownedAccounts.data?.accounts.find(account =>
    account.id === (savedAccountId || status?.account_id || ownedAccounts.data?.active_id));
  const expiredStoredToken = !!selectedStoredAccount?.token_expires_at_ms
    && selectedStoredAccount.token_expires_at_ms <= Date.now();
  const rejectedToken = /\b(rejected|expired|not logged in|revoked|identity could not)\b/i
    .test(status?.message || '');
  const persistedButUnverified = !!selectedStoredAccount?.connected
    && !expiredStoredToken && !rejectedToken && !connected;
  const savedPending = (!!savedAccountId && !(connected && status?.account_id === savedAccountId))
    || persistedButUnverified;
  const selectedDifferentAccount = !!savedAccountId && savedPending
    && !!status?.account_id && status.account_id !== savedAccountId;
  const cardTitle = connected
    ? 'Zerodha Kite Active'
    : savedPending ? (verificationTimedOut || !savedAccountId
      ? 'Kite session saved · unverified' : 'Kite session saved · verifying')
    : unknown
      ? permissionDenied ? 'Kite access denied' : 'Kite session check unavailable'
      : checking ? 'Checking Kite session…'
      : 'Zerodha Kite Offline';

  const cardDetail = connected
    ? `Signed in as ${status?.user_name || 'Kite User'}${status?.kite_user_id ? ` (${status.kite_user_id})` : ''} · Broker session verified. Trading still requires independent safety checks.`
    : savedPending ? (selectedDifferentAccount
      ? 'The login was saved for a different broker account than the active one. Select the saved account in Broker accounts to make it active; this does not authorize new orders.'
      : verificationTimedOut
        ? 'The login was saved, but a usable session could not be verified. Check the active account and Kite connectivity; do not assume live access.'
        : 'Broker token saved. Checking the active Kite session and account permissions; connected status is not yet verified.')
    : unknown
      ? permissionDenied
        ? 'Kite refused the session check. Check the API app permissions and registered outbound IP, then retry or reconnect.'
        : checkMessage
      : checking ? 'Reaching Zerodha Kite endpoints to verify session status.'
      : status?.message || 'Session expired or disconnected. Reconnect below to resume live data and trading.';

  // After a saved login, lead with verification and explicit recovery, not
  // a second credential form that misleadingly says "Sign in" by default.
  const isLoginVisible = (!connected && !savedPending) || showRelogin
    || (loginRequested && !savedPending);

  return createPortal(
    <div className="kite-session-overlay" data-leaving={leaving ? 'true' : 'false'}
      role="dialog" aria-modal="true" aria-label="Zerodha Kite Status" onClick={handleDismiss}>
      <div ref={panelRef} className="kite-session-panel" onClick={e => e.stopPropagation()} onKeyDown={e => {
        if (e.key !== 'Tab') return;
        const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,a[href]') ?? []).filter(el => el.getClientRects().length > 0);
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }}>
        <header className="kite-session-header">
          <div className="kite-session-mark" data-connected={connected}>{checking ? <KiteLoader size={22} /> : connected ? '✓' : 'K'}</div>
          <div><h2>{cardTitle}</h2><p>{status?.kite_user_id || status?.user_name || 'Broker connection'}</p></div>
          <div className="kite-session-header-actions">
            {onOpenAccountSettings && (
              <button type="button" className="kite-session-icon-btn" aria-label="Account & API Settings" title="Account & API Settings" onClick={() => { closeCard(); onOpenAccountSettings(); }}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
              </button>
            )}
            <button type="button" className="kite-session-close" aria-label="Close broker status" onClick={handleDismiss}>×</button>
          </div>
        </header>
        <div className="kite-session-body">
          <div className="kite-session-summary" data-connected={connected}>
            <span aria-label={connected ? 'Kite connected' : savedPending || unknown ? 'Kite session unverified' : checking ? 'Kite session checking' : 'Kite offline'}>
              {connected ? 'CONNECTED' : savedPending ? (verificationTimedOut || !savedAccountId ? 'UNVERIFIED' : 'VERIFYING') : unknown ? 'UNVERIFIED' : checking ? 'CHECKING' : 'OFFLINE'}
            </span>
            <p>{connected ? `Signed in as ${status?.user_name || status?.kite_user_id || 'Kite user'}.` : savedPending ? cardDetail : unknown ? cardDetail : checking ? 'Checking your broker session…' : 'Sign in to connect your broker account.'}</p>
          </div>
          {(unknown || savedPending) && <button type="button" disabled={isFetching} onClick={() => void refetch()}>{isFetching ? 'Checking session…' : 'Verify broker connection'}</button>}
          {savedPending && !selectedDifferentAccount && !connected && !loginRequested &&
            <button type="button" onClick={() => setShowRelogin(v => !v)}>
              {showRelogin ? 'Hide broker re-login' : 'Re-login if verification fails'}
            </button>}
          {selectedDifferentAccount && savedAccountId && <div className="kite-session-account-actions">
            <button type="button" disabled={activateAccount.isPending}
              onClick={() => activateAccount.mutate(savedAccountId, {onSuccess: () => {
                setVerificationTimedOut(false);
                void refetch();
              }})}>
              {activateAccount.isPending ? 'Checking account-switch safety…' : 'Make saved broker account active'}
            </button>
            {activateAccount.error && <p role="alert">{activateAccount.error.message}</p>}
            <small>Changing the active broker account requires Sterling's broker-exposure and pending-order safety checks. It does not enable live entries.</small>
          </div>}
          {isLoginVisible && <KiteAssistedLogin key={loginRequested ? `request-${loginAccountId || 'choose'}` : 'normal'} accountId={effectiveAccountId} autoStart={loginRequested} onSaved={onSessionSaved} />}
          {connected && <div className="kite-session-account-actions">
            {!loginRequested && <button type="button" onClick={() => { setShowRelogin(v => !v); logout.reset?.(); }}>{showRelogin ? 'Hide broker login' : 'Log in to another account'}</button>}
            <button type="button" disabled={logout.isPending} onClick={() => logout.mutate(undefined, { onSuccess: () => {
              setShowRelogin(false);
              notifyOrder({ kind: 'complete', title: 'Kite logged out', message: 'Your Sterling login is still active.' });
            } })}>{logout.isPending ? <><ButtonLoader /> Logging out…</> : 'Logout of Kite'}</button>
          </div>}
          {connected && logout.error && <p role="alert">{logout.error.message}</p>}
          <EgressAddressRow />
          {isLoginVisible && <details className="kite-session-disclosure">
            <summary>Manual login & recovery</summary>
            <div className="kite-session-disclosure-body">
              <button type="button" disabled={kiteLogin.opening} onClick={kiteLogin.open}>
                {kiteLogin.phase === 'waiting' ? 'Finish login in the Kite window' : kiteLogin.opening ? 'Opening Login…' : 'Manual Kite login ↗'}
              </button>
              {canAutoRecover && <button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate({})}>{refresh.isPending ? 'Renewing…' : 'Try session renewal'}</button>}
              {kiteLogin.phase === 'waiting' && <p>Finish signing in in the Kite window.<button type="button" onClick={kiteLogin.dismiss}>Start login again</button></p>}
              {kiteLogin.error && <p role="alert">{kiteLogin.error}</p>}
              {kiteLogin.loginUrl && (kiteLogin.phase === 'failed' || kiteLogin.phase === 'waiting') && <KiteLoginLinkFallback url={kiteLogin.loginUrl} onContinue={kiteLogin.waitForLogin} />}
              <details className="kite-session-disclosure"><summary>Recover a callback link</summary>
                <p>Paste the full callback address if the browser could not return to Sterling.</p>
                <form onSubmit={e => { e.preventDefault(); if (reqToken.trim() && !gen.isPending) gen.mutate({ request_token: reqToken.trim(), account_id: effectiveAccountId }, { onSuccess: () => { setReqToken(''); closeCard(); } }); }}>
                  <input aria-label="Full callback URL or request_token" placeholder="Full callback URL or request_token" value={reqToken} onChange={e => setReqToken(e.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} />
                  <button type="submit" disabled={!reqToken.trim() || gen.isPending}>{gen.isPending ? 'Connecting…' : 'Connect'}</button>
                </form>
                {gen.error && <p role="alert">{gen.error.message}</p>}
              </details>
            </div>
          </details>}
        </div>

      </div>
    </div>, document.body,
  );
}

export default KiteSessionGuard;

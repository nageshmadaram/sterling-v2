import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useKiteStatusCardStore } from '../../store/useKiteStatusCardStore';

/** A Telegram link is a navigation hint, never proof of account ownership. */
export function BrokerLoginEntry() {
  const qc = useQueryClient();
  const [error, setError] = useState('');
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get('broker_login') !== '1') return;
    const account = url.searchParams.get('broker_account');
    const profile = url.searchParams.get('broker_profile');
    let active = true;
    const open = async () => {
      try {
        const me = await api.get<{ user_id: string }>('/api/v1/auth/me');
        if (!active) return;
        if (!me.user_id) throw new Error('Sign in to Sterling before opening broker login.');
        if (profile && me.user_id !== profile) throw new Error('Sign in with the Sterling profile that owns this broker login.');
        const owned = await api.get<{ accounts: { id: string }[] }>('/api/v1/kite/accounts');
        if (!active) return;
        if (account && !owned.accounts.some(a => a.id === account)) throw new Error('This broker login is not linked to your Sterling profile.');
        qc.setQueryData(['kite-accounts'], owned);
        for (const key of ['broker_login', 'broker_account', 'broker_profile']) url.searchParams.delete(key);
        window.history.replaceState(window.history.state, '', url.href);
        useKiteStatusCardStore.getState().requestLogin(account || undefined);
      } catch (failure) {
        if (active) setError((failure as Error).message);
      }
    };
    void open();
    return () => { active = false; };
  }, [qc]);
  return error ? <div className="kite-session-overlay"><div className="kite-session-panel" role="alert">
    <div className="kite-session-body"><strong>Broker login</strong><p>{error}</p><button type="button" onClick={() => setError('')}>Close</button></div>
  </div></div> : null;
}

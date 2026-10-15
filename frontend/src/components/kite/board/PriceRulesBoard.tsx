import { formatInstrument } from '../../../lib/instrument';
import { priceRulesEntryReason, priceRulesNeedsLogin } from '../priceRulesStatus';
import { useSavedSelection } from '../../../hooks/useSavedSelection';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { useIsSimulation } from '../../../store/useViewStore';
import { openSettingsSection } from '../config/registry';
import type { Snapshot } from '../PriceRulesPanel';

export function PriceRulesBoard() {
  const simulation = useIsSimulation();
  const [search, setSearch] = useSavedSelection<string>('price_rules.search', '');
  const [history, setHistory] = useSavedSelection<boolean>('price_rules.history', false);
  const query = useQuery({queryKey:['price-rules'], queryFn:() => api.get<Snapshot>('/api/v1/price-rules'),
    refetchInterval:3000, enabled:!simulation});
  const terminal = new Set(['CLOSED','CANCELLED','REFUSED','EXPIRED']);
  const rows = (query.data?.rules || []).filter(r => (history || !terminal.has(r.state)) &&
    `${r.symbol} ${r.watch} ${r.state}`.toLowerCase().includes(search.toLowerCase()));
  return <section className="price-rules-board" aria-label="Price Rules signals">
    <div className="price-rules-board-toolbar">
      <input aria-label="Search Price Rules signals" placeholder="Search contract or state" value={search} onChange={e => setSearch(e.target.value)} />
      <label><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> History</label>
      <button type="button" onClick={() => openSettingsSection('priceRules')}>Create / manage rules ↗</button>
    </div>
    <p>Signals follow your published plans. Armed conditions wait for fresh broker prices; each plan fires once.</p>
    {simulation ? <p role="status">Price Rules signals use the Live broker account. Switch to Live to view them.</p> : query.isLoading ?
      <p role="status">Loading Price Rules signals…</p> : query.isError ?
      <div role="alert">Could not load signals: {(query.error as Error).message} <button type="button" className="sterling-retry-button" onClick={() => query.refetch()}>Retry</button></div> : <>
      {query.data && !query.data.entry_allowed && <p role="status">New entries: {priceRulesEntryReason(query.data.entry_blocker).replace(/\.+$/, '')}. Existing plans and protection remain visible. <button onClick={() => openSettingsSection(priceRulesNeedsLogin(query.data?.entry_blocker) ? 'account' : 'mode')}>{priceRulesNeedsLogin(query.data?.entry_blocker) ? 'Reconnect Kite ↗' : 'Trading Config ↗'}</button></p>}
      {!rows.length ? <div className="price-rules-empty"><h3>{search ? 'No matching signals' : 'No Price Rules signals yet'}</h3>
        <p>{search ? 'Try another contract or include history.' : 'Create an exact option plan, then arm it. Its trigger, entry, stop, target and state will appear here.'}</p>
        {!search && <button onClick={() => openSettingsSection('priceRules')}>Create entry plan</button>}</div> :
        <div className="price-rule-signal-list">{rows.map(r => {
          const remaining = r.filled - Object.values(r.exits || {}).reduce((n,e) => n + e.filled,0);
          return <article key={r.id} className="price-rule-signal" data-state={r.state}>
            <header><strong>{formatInstrument(r.symbol)}</strong><span>{r.state.replaceAll('_',' ')}</span></header>
            <small>{r.exchange} · Account {r.account_id} · Revision {r.revision}</small>
            <p>{r.comparison === 'MANUAL' ? 'Manual entry after arming' : `${r.watch} ${r.comparison} ${r.level}`}</p>
            <dl>
              <div><dt>Entry</dt><dd>{r.entry_price ?? '—'}</dd></div>
              <div><dt>Stop</dt><dd>{r.stop ?? 'Set from fill'}</dd></div>
              <div><dt>Target</dt><dd>{r.target || 'Off'}</dd></div>
              <div><dt>Requested / filled</dt><dd>{r.quantity} / {r.filled}</dd></div>
              <div><dt>Remaining</dt><dd>{remaining}</dd></div>
              <div><dt>Protection</dt><dd>{r.protection_pending ? 'Pending verification' : r.gtt_id ? `GTT · ${r.gtt_qty} units` : remaining > 0 ? 'Not confirmed' : 'No open holding'}</dd></div>
            </dl>
            {r.error && <p role="alert">{r.error}</p>}
            <button onClick={() => openSettingsSection('priceRules')}>Review / manage plan</button>
          </article>;
        })}</div>}
    </>}
  </section>;
}

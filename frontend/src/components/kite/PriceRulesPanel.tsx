import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useKiteOptionChain } from '../../hooks/useKiteOptionChain';
import { useIsSimulation } from '../../store/useViewStore';

type Rule = { id: string; revision: number; state: string; account_id: string; exchange: string; symbol: string;
  quantity: number; filled: number; exits: Record<string, { filled: number; price?: number }>; option_type: string;
  gtt_id?: number; gtt_qty?: number; protection_pending?: boolean; error: string;
  comparison: string; watch: string; level: number; stop?: number; target?: number; entry_price?: number };
type Snapshot = { name: string; entry_allowed: boolean; entry_blocker: string; rules: Rule[] };
const BASE = '/api/v1/price-rules';
const KEY = ['price-rules'];

export function PriceRulesPanel() {
  const simulation = useIsSimulation();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: KEY, queryFn: () => api.get<Snapshot>(BASE), refetchInterval: 3000, enabled: !simulation });
  const [underlying, setUnderlying] = useState('NIFTY 50');
  const chain = useKiteOptionChain(simulation ? "" : underlying);
  const [expiry, setExpiry] = useState('');
  const [symbol, setSymbol] = useState('');
  const [exchange, setExchange] = useState<'NFO' | 'BFO'>('NFO');
  const [lots, setLots] = useState(1);
  const [stop, setStop] = useState(20);
  const [target, setTarget] = useState(40);
  const [comparison, setComparison] = useState('MANUAL');
  const [watch, setWatch] = useState('');
  const [level, setLevel] = useState(0);
  const [percent, setPercent] = useState(100);
  const exportCsv = () => {
    const records = [['Rule', 'Account', 'Exchange', 'Symbol', 'State', 'Requested', 'Filled', 'Remaining', 'Entry', 'Gross realized P&L', 'GTT', 'Protected units']];
    for (const r of query.data?.rules || []) {
      const sold = Object.values(r.exits).reduce((n, o) => n + o.filled, 0);
      const gross = Object.values(r.exits).reduce((n, o) => n + o.filled * ((o.price || 0) - (r.entry_price || 0)), 0);
      records.push([r.id, r.account_id, r.exchange, r.symbol, r.state, String(r.quantity), String(r.filled), String(r.filled - sold), String(r.entry_price || 0), String(gross), String(r.gtt_id || ''), String(r.gtt_qty || 0)]);
    }
    const csv = records.map(row => row.map(v => `"${v.replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = 'sterling-price-rules.csv'; link.click(); URL.revokeObjectURL(url);
  };
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const publish = useMutation({ mutationFn: () => api.post<Rule>(`${BASE}/plans`, {
    exchange, symbol, lots, stop_points: stop, target_points: target, comparison, watch, level,
  }), onSuccess: refresh });
  const command = useMutation({ mutationFn: ({ rule, action }: { rule: Rule; action: string }) =>
    api.post(`${BASE}/plans/${encodeURIComponent(rule.id)}/command`, { revision: rule.revision, action }), onSuccess: refresh });
  const exit = useMutation({ mutationFn: ({ id, size }: { id: string; size: number }) =>
    api.post(`${BASE}/positions/${encodeURIComponent(id)}/exit`, { percent: size }), onSuccess: refresh });
  const rows = chain.data?.chain[expiry || chain.data?.expiries[0]?.date || ''] || [];
  const ready = !simulation && !query.isError && !!query.data?.entry_allowed;
  const busy = publish.isPending || command.isPending || exit.isPending;
  const error = publish.error || command.error || exit.error;
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const element = e.target as HTMLElement;
      if (simulation || busy || query.isError || e.repeat || e.ctrlKey || e.altKey || e.metaKey || element?.isContentEditable ||
          ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(element?.tagName)) return;
      if (['1', '2', '3'].includes(e.key)) setLots(Number(e.key));
      if (e.key === 'Escape') {
        e.preventDefault();
        const open = query.data?.rules.filter(r => r.filled > Object.values(r.exits).reduce((n, o) => n + o.filled, 0)) || [];
        // Requests remain bounded and serialized by the backend, including keyboard exits.
        void (async () => { for (const r of open) { try { await exit.mutateAsync({ id: r.id, size: 100 }); } catch { break; } } })();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [simulation, busy, query.data, exit]);
  if (simulation) return <section style={{ padding: 20 }}>Price Rules uses a real broker account. Switch to Live to configure this strategy.</section>;
  return <section style={{ padding: 20, color: 'var(--k-text)', display: 'grid', gap: 14 }}>
    <h2>Price Rules — real trading</h2>
    <button disabled={!query.data || query.isError} onClick={exportCsv}>Export confirmed history CSV (gross, before fees)</button>
    <p>Long index calls and puts. Every entry requires an exact plan review and broker stop. Sell actions only close this strategy’s holdings; naked shorts are unsupported.</p>
    {query.isError ? <p role="alert">Price Rules unavailable: {(query.error as Error).message}</p> :
      !ready && <p role="status">New entries blocked: {query.data?.entry_blocker || 'Checking account and safety…'}</p>}
    {error && <p role="alert">{(error as Error).message}</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
      <label>Index <select value={underlying} onChange={e => { setUnderlying(e.target.value); setExpiry(''); setSymbol(''); setExchange(e.target.value === 'SENSEX' || e.target.value === 'BANKEX' ? 'BFO' : 'NFO'); }}>
        {['NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX', 'BANKEX'].map(v => <option key={v}>{v}</option>)}
      </select></label>
      <label>Expiry <select value={expiry || chain.data?.expiries[0]?.date || ''} onChange={e => { setExpiry(e.target.value); setSymbol(''); }}>
        {chain.data?.expiries.map(e => <option key={e.date} value={e.date}>{e.label}</option>)}
      </select></label>
      <label>Exact option <input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Select a call or put below" /></label>
      <label>Exchange <select value={exchange} onChange={e => setExchange(e.target.value as 'NFO' | 'BFO')}><option>NFO</option><option>BFO</option></select></label>
      <label>Lots <input type="number" min={1} max={30} value={lots} onChange={e => setLots(Number(e.target.value))} /></label>
      <label>Stop points <input type="number" min={0.05} step={0.05} value={stop} onChange={e => setStop(Number(e.target.value))} /></label>
      <label>Target points (0 off) <input type="number" min={0} step={0.05} value={target} onChange={e => setTarget(Number(e.target.value))} /></label>
    </div>
    {chain.isError && <p role="alert">Option chain unavailable: {(chain.error as Error).message}</p>}
    <details><summary>Choose call or put from the live chain</summary><table><thead><tr><th>Strike</th><th>Call</th><th>Put</th></tr></thead><tbody>
      {rows.map(row => <tr key={row.strike}><td>{row.strike}</td>
        <td>{row.call && <button onClick={() => setSymbol(row.call!.symbol)}>{row.call.symbol} · {row.call.ltp}</button>}</td>
        <td>{row.put && <button onClick={() => setSymbol(row.put!.symbol)}>{row.put.symbol} · {row.put.ltp}</button>}</td></tr>)}
    </tbody></table></details>
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
      <label>Entry condition <select value={comparison} onChange={e => setComparison(e.target.value)}>
        <option value="MANUAL">Manual after arming</option>{['>=', '<=', '>', '<'].map(v => <option key={v}>{v}</option>)}
      </select></label>
      {comparison !== 'MANUAL' && <><label>Watch instrument <input value={watch} onChange={e => setWatch(e.target.value)} placeholder="NSE:NIFTY 50 or NFO:exact option" /></label>
        <label>Trigger level <input type="number" value={level} onChange={e => setLevel(Number(e.target.value))} /></label></>}
      <button disabled={busy || !symbol || !ready} onClick={() => publish.mutate()}>Publish entry plan</button>
      <label>Exit size <select value={percent} onChange={e => setPercent(Number(e.target.value))}>{[25, 50, 75, 100].map(v => <option key={v} value={v}>{v}%</option>)}</select></label>
    </div>
    <p>Plans fire once. Conditions use fresh server prices. Account limits and entry brakes apply. Keys 1/2/3 select lots; Escape requests full owned exits outside input fields.</p>
    <table><thead><tr><th>Exact contract / account</th><th>Condition / state</th><th>Requested / filled / remaining</th><th>Broker protection</th><th>Actions</th></tr></thead><tbody>
      {query.data?.rules.map(r => { const left = r.filled - Object.values(r.exits).reduce((n, o) => n + o.filled, 0); return <tr key={r.id}>
        <td>{r.symbol}<br />{r.account_id}</td><td>{r.comparison === 'MANUAL' ? 'Manual' : `${r.watch} ${r.comparison} ${r.level}`}<br />{r.state}{r.error && <p role="alert">{r.error}</p>}</td>
        <td>{r.quantity} / {r.filled} / {left}</td><td>{r.protection_pending ? 'UNKNOWN / pending' : r.gtt_id ? `GTT ${r.gtt_id}: ${r.gtt_qty} units` : 'None'}<br />Stop {r.stop ?? 'from fill'} · Target {r.target || 'off'}</td>
        <td>{['DRAFT', 'PAUSED'].includes(r.state) && <button disabled={!ready || busy} onClick={() => command.mutate({ rule: r, action: 'arm' })}>Arm revision {r.revision}</button>}
          {r.state === 'ARMED' && <button disabled={busy} onClick={() => command.mutate({ rule: r, action: 'pause' })}>Pause</button>}
          {['DRAFT', 'PAUSED', 'ARMED'].includes(r.state) && <button disabled={busy} onClick={() => command.mutate({ rule: r, action: 'cancel' })}>Cancel plan</button>}
          {left > 0 && <button disabled={busy || query.isError} onClick={() => exit.mutate({ id: r.id, size: percent })}>Exit owned {percent}%</button>}</td>
      </tr>; })}
    </tbody></table>
  </section>;
}

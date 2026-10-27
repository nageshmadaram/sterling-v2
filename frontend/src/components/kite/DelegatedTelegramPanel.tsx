import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { configurationAllowed, type ConfigurationCapabilities } from '../../hooks/useConfigurationCapabilities';

type Target = { id: string; label: string; chat_id: string; enabled: boolean; reachable: boolean };
const BASE = '/api/v1/kite/telegram';
function Destination({ target, capabilities }: { target: Target; capabilities: ConfigurationCapabilities }) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(target.label);
  const [chat, setChat] = useState(target.chat_id);
  const [enabled, setEnabled] = useState(target.enabled);
  const [notice, setNotice] = useState('');
  const edit = configurationAllowed(capabilities, 'telegram', 'recipients', target.id);
  const test = configurationAllowed(capabilities, 'telegram', 'test', target.id);
  useEffect(() => { setLabel(target.label); setChat(target.chat_id); setEnabled(target.enabled); setNotice(''); },
    [target.label, target.chat_id, target.enabled, edit]);
  const save = useMutation({ mutationFn: () => api.put(`${BASE}/${encodeURIComponent(target.id)}`, { label, chat_id: chat, enabled }),
    onSuccess: () => { setNotice('Destination saved. No message was sent.'); void qc.invalidateQueries({ queryKey: ['delegated-telegram'] }); } });
  const send = useMutation({ mutationFn: () => api.post(`${BASE}/${encodeURIComponent(target.id)}/test`, {}),
    onSuccess: () => { setNotice('Test request completed. Check delivery status.'); void qc.invalidateQueries({ queryKey: ['delegated-telegram'] }); } });
  return <form className="bac-card" style={{ padding: 16 }} onSubmit={event => { event.preventDefault(); if (edit) save.mutate(); }}>
    <h3>{target.label || 'Alert destination'}</h3>
    <label className="bac-label">Label<input className="bac-input" value={label} readOnly={!edit} onChange={e => setLabel(e.target.value)} /></label>
    <label className="bac-label">Telegram recipient<input className="bac-input" value={chat} readOnly={!edit} onChange={e => setChat(e.target.value)} /></label>
    <label><input type="checkbox" checked={enabled} disabled={!edit} onChange={e => setEnabled(e.target.checked)} /> Enabled</label>
    <div className="bac-row-gap" style={{ marginTop: 12 }}>
      {edit && <button className="bac-btn" disabled={save.isPending} type="submit">Save destination</button>}
      {test && <button className="bac-btn" type="button" disabled={send.isPending || chat !== target.chat_id}
        onClick={() => { if (window.confirm(`Send a test message to saved recipient ${target.chat_id}?`)) send.mutate(); }}>Send test to {target.chat_id}</button>}
    </div>
    {(save.error || send.error) && <p role="alert">{(save.error || send.error)?.message}</p>}
    {notice && <p role="status">{notice}</p>}
  </form>;
}
export function DelegatedTelegramPanel({ capabilities }: { capabilities: ConfigurationCapabilities }) {
  const targets = useQuery({ queryKey: ['delegated-telegram'], queryFn: () => api.get<{ targets: Target[] }>(BASE), retry: false });
  if (targets.isError) return <p role="alert">{targets.error.message}</p>;
  return <section aria-label="Telegram destinations"><h2>Telegram destinations</h2>
    <p>Manage permitted recipients. Bot credentials are managed by your administrator.</p>
    {targets.isLoading && <p role="status">Loading destinations…</p>}
    {targets.data?.targets.map(target => <Destination key={target.id} target={target} capabilities={capabilities} />)}
    {targets.data?.targets.length === 0 && <p>No alert destination is assigned.</p>}
  </section>;
}

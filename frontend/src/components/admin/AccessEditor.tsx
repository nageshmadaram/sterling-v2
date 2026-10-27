import {useUnsavedDraftGuard} from '../kite/config/unsavedDraftGuard';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import type { ConfigurationCapabilities, ConfigurationSection, ConfigurationAction, ConfigurationGrant } from '../../hooks/useConfigurationCapabilities';

type Features = { kite: boolean; tv: boolean; radio: boolean };
type FeaturePolicy = { user_id: string; revision: number; features: Features };
const BASE = '/api/v1/auth/users';
const SECTIONS: ConfigurationSection[] = ['brokers', 'telegram', 'integrations'];

function FeatureEditor({ userId }: { userId: string }) {
  const qc = useQueryClient();
  const key = ['admin-features', userId];
  const query = useQuery({ queryKey: key, queryFn: () => api.get<FeaturePolicy>(`${BASE}/${encodeURIComponent(userId)}/features`) });
  const [draft, setDraft] = useState<FeaturePolicy | null>(null);
  const dirty=!!draft&&!!query.data&&JSON.stringify(draft.features)!==JSON.stringify(query.data.features);
  useUnsavedDraftGuard('admin-features:'+userId,dirty);
  useEffect(() => { if (query.data&&!dirty) setDraft(query.data); }, [query.data]);
  const save = useMutation({ mutationFn: () => api.put<FeaturePolicy>(`${BASE}/${encodeURIComponent(userId)}/features`,
    { features: draft?.features, expected_revision: draft?.revision }),
    onSuccess: data => { setDraft(data); qc.setQueryData(key, data); void qc.invalidateQueries({ queryKey: ['admin-configuration', userId] }); } });
  return <section><h3>Workspaces</h3>
    {query.isLoading && <p role="status">Loading grants…</p>}
    {query.isError && <p role="alert">{query.error.message}</p>}
    {draft && <form onSubmit={e => { e.preventDefault(); save.mutate(); }}>
      <div className="admin-checks">{(['kite', 'radio', 'tv'] as const).map(feature => <label key={feature}>
        <input type="checkbox" checked={draft.features[feature]} disabled={save.isPending} onChange={e => setDraft({ ...draft, features: { ...draft.features, [feature]: e.target.checked } })} />
        {feature === 'kite' ? 'Kite' : feature === 'tv' ? 'Space (TV)' : 'Echo (Radio)'}</label>)}</div>
      <button className="bac-btn bac-btn-primary" disabled={save.isPending}>Save features</button>
    </form>}
    {save.isSuccess && <p role="status">Feature grants saved.</p>}
    {save.isError && <p role="alert">{save.error.message} <button className="bac-btn" onClick={() => { save.reset(); void query.refetch().then(result=>{if(result.data)setDraft(result.data);}); }}>Reload grants</button></p>}
  </section>;
}

function SectionEditor({ userId, section, policy }: { userId: string; section: ConfigurationSection; policy: ConfigurationCapabilities }) {
  const qc = useQueryClient();
  const [original,setOriginal]=useState(policy.sections[section]);
  const [expectedRevision,setExpectedRevision]=useState(policy.revision);
  const [visible, setVisible] = useState(policy.sections[section].visible);
  const [grants, setGrants] = useState<ConfigurationGrant[]>(policy.sections[section].grants.map(({ action, resource_id, expires_at_ms }) => ({ action, resource_id, expires_at_ms })));
  const dirty=visible!==original.visible||JSON.stringify(grants)!==JSON.stringify(original.grants.map(({action,resource_id,expires_at_ms})=>({action,resource_id,expires_at_ms})));
  useUnsavedDraftGuard('admin-configuration:'+userId+':'+section,dirty);
  useEffect(()=>{
    const incoming=policy.sections[section];
    if(!dirty){setOriginal(incoming);setVisible(incoming.visible);setGrants(incoming.grants.map(({action,resource_id,expires_at_ms})=>({action,resource_id,expires_at_ms})));setExpectedRevision(policy.revision);}
    else if(JSON.stringify(incoming)===JSON.stringify(original))setExpectedRevision(policy.revision);
  },[policy]);

  const eligibleActions: ConfigurationAction[] = section === 'telegram' ? ['recipients', 'test'] : section === 'brokers' ? ['create', 'update'] : ['create', 'update', 'test'];
  const save = useMutation({ mutationFn: () => api.put<ConfigurationCapabilities>(`${BASE}/${encodeURIComponent(userId)}/configuration-access`,
    { section, visible, grants:visible?grants:[], expected_revision: expectedRevision }),
    onSuccess: data => { setOriginal(data.sections[section]);setVisible(data.sections[section].visible);setGrants(data.sections[section].grants.map(({action,resource_id,expires_at_ms})=>({action,resource_id,expires_at_ms})));setExpectedRevision(data.revision);qc.setQueryData(['admin-configuration', userId], data); void qc.invalidateQueries({ queryKey: ['admin-features', userId] }); void qc.invalidateQueries({ queryKey: ['sterling-configuration-capabilities'] }); } });
  return <form className="admin-section" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
    <h3>{section === 'brokers' ? 'Broker settings' : section === 'telegram' ? 'Telegram settings' : 'Integration settings'}</h3>
    <label><input type="checkbox" checked={visible} onChange={e => setVisible(e.target.checked)} /> Visible</label>

    {visible && <fieldset disabled={save.isPending}><legend>Allowed actions</legend>
      <div className="admin-action-options">{eligibleActions.map(value => <label key={value}><input type="checkbox" checked={grants.some(grant => grant.action === value && grant.resource_id === 'own')} onChange={event => setGrants(event.target.checked ? [...grants, { action: value, resource_id: 'own', expires_at_ms: null }] : grants.filter(grant => !(grant.action === value && grant.resource_id === 'own')))} />{value === 'create' ? 'Add connections' : value === 'update' ? 'Edit assigned connections' : value === 'recipients' ? 'Manage alert recipients' : 'Test connections'}</label>)}</div>

      <ul>{grants.filter(grant => grant.resource_id !== 'own').map(grant => <li key={`${grant.action}:${grant.resource_id}`}>{grant.action} · {grant.resource_id}{' '}
        <button type="button" className="bac-btn" onClick={() => setGrants(grants.filter(item => item.action !== grant.action || item.resource_id !== grant.resource_id))}>Remove scope</button>
      </li>)}</ul>
    </fieldset>}
    <button className="bac-btn bac-btn-primary" disabled={save.isPending}>Save permissions</button>
    {save.isError && <p role="alert">{save.error.message} <button type="button" className="bac-btn" onClick={() => {const incoming=policy.sections[section];setOriginal(incoming);setVisible(incoming.visible);setGrants(incoming.grants.map(({action,resource_id,expires_at_ms})=>({action,resource_id,expires_at_ms})));setExpectedRevision(policy.revision);void qc.invalidateQueries({queryKey:['admin-configuration',userId]});}}>Reload access</button></p>}
  </form>;
}

export function AccessEditor({ userId }: { userId: string }) {
  const query = useQuery({ queryKey: ['admin-configuration', userId], queryFn: () => api.get<ConfigurationCapabilities>(`${BASE}/${encodeURIComponent(userId)}/configuration-access`) });
  return <div className="admin-access"><FeatureEditor userId={userId} /><h2>Settings permissions</h2>
    {query.isLoading && <p role="status">Loading settings access…</p>}
    {query.isError && <p role="alert">{query.error.message}</p>}
    {query.data?.administrator ? <p>The designated administrator retains configuration authority.</p> : query.data && SECTIONS.map(section =>
      <SectionEditor key={`${userId}:${section}`} userId={userId} section={section} policy={query.data!} />)}
  </div>;
}

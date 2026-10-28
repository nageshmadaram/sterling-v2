import type {ConfigurationAction,ConfigurationGrant,ConfigurationSection} from '../../hooks/useConfigurationCapabilities';
export type InitialAccess = {
  features: {kite:boolean;radio:boolean;tv:boolean};
  visibility: {kite:boolean;radio:boolean;tv:boolean};
  configuration: Record<ConfigurationSection,{visible:boolean;grants:ConfigurationGrant[]}>;
};
export const newAccess=():InitialAccess=>({features:{kite:false,radio:false,tv:false},visibility:{kite:true,radio:true,tv:true},configuration:{brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}}});
const labels={brokers:'Broker settings',telegram:'Telegram settings',integrations:'Integration settings'};
const actions:Record<ConfigurationSection,ConfigurationAction[]>={brokers:['create','update'],telegram:['recipients','test'],integrations:['create','update','test']};
const actionLabels={create:'Add connections',update:'Edit own connections',test:'Test connections',recipients:'Manage alert recipients',reveal:'Reveal secrets'};
export function InitialAccessFields({value,onChange}:{value:InitialAccess;onChange:(next:InitialAccess)=>void}){
  return <div className="admin-initial-access">
    <fieldset><legend>Workspaces</legend><p>Grant access independently from showing each workspace in the chooser. Hidden workspaces cannot be requested by this user.</p>
      <div className="admin-workspace-access-head"><strong>Workspace</strong><span>Allow access</span><span>Show in menu</span></div>
      <div className="admin-workspace-access-grid">{(['kite','radio','tv'] as const).map(feature=><div className="admin-workspace-access-row" key={feature}>
        <strong>{feature==='kite'?'Kite · trading':feature==='tv'?'Space · TV':'Echo · radio'}</strong>
        <label><input type="checkbox" aria-label={`Allow ${feature} access`} checked={value.features[feature]} onChange={e=>onChange({...value,features:{...value.features,[feature]:e.target.checked}})}/><span className="admin-sr-only">Allow access</span></label>
        <label><input type="checkbox" aria-label={`Show ${feature} in workspace chooser`} checked={value.visibility[feature]} onChange={e=>onChange({...value,visibility:{...value.visibility,[feature]:e.target.checked}})}/><span className="admin-sr-only">Show in menu</span></label>
      </div>)}</div>
    </fieldset>
    <fieldset><legend>Settings permissions</legend><p>Settings access is separate from workspace access. Permissions apply to this user’s own connections.</p>
      {(['brokers','telegram','integrations'] as const).map(section=>{
        const policy=value.configuration[section];
        const update=(next:typeof policy)=>onChange({...value,configuration:{...value.configuration,[section]:next}});
        return <section className="admin-initial-section" key={section}><label><input type="checkbox" checked={policy.visible} onChange={e=>update({visible:e.target.checked,grants:e.target.checked?policy.grants:[]})}/>{labels[section]}</label>
          {policy.visible&&<div className="admin-action-options">{actions[section].map(action=><label key={action}><input type="checkbox" checked={policy.grants.some(g=>g.action===action)} onChange={e=>update({...policy,grants:e.target.checked?[...policy.grants,{action,resource_id:'own',expires_at_ms:null}]:policy.grants.filter(g=>g.action!==action)})}/>{actionLabels[action]}</label>)}</div>}
        </section>;
      })}
    </fieldset>
  </div>;
}

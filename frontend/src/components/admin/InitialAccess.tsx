import type {ConfigurationAction,ConfigurationGrant,ConfigurationSection} from '../../hooks/useConfigurationCapabilities';
export type InitialAccess = {
  features: {kite:boolean;radio:boolean;tv:boolean};
  configuration: Record<ConfigurationSection,{visible:boolean;grants:ConfigurationGrant[]}>;
};
export const newAccess=():InitialAccess=>({features:{kite:false,radio:false,tv:false},configuration:{brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}}});
const labels={brokers:'Broker settings',telegram:'Telegram settings',integrations:'Integration settings'};
const actions:Record<ConfigurationSection,ConfigurationAction[]>={brokers:['create','update'],telegram:['recipients','test'],integrations:['create','update','test']};
const actionLabels={create:'Add connections',update:'Edit own connections',test:'Test connections',recipients:'Manage alert recipients',reveal:'Reveal secrets'};
export function InitialAccessFields({value,onChange}:{value:InitialAccess;onChange:(next:InitialAccess)=>void}){
  return <div className="admin-initial-access">
    <fieldset><legend>Workspace access</legend><p>Choose the workspaces available when this user first signs in.</p>
      <div className="admin-checks">{(['kite','radio','tv'] as const).map(feature=><label key={feature}><input type="checkbox" checked={value.features[feature]} onChange={e=>onChange({...value,features:{...value.features,[feature]:e.target.checked}})}/>{feature==='kite'?'Kite · trading workspace':feature==='tv'?'Space · television':'Echo · radio'}</label>)}</div>
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

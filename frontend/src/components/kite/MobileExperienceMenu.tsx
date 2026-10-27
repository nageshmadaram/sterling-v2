import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {useExperienceOptions} from '../../store/useExperienceOptions';
import {useKiteStatusCardStore} from '../../store/useKiteStatusCardStore';
import {NAV_OPTIONS} from '../../store/useExperienceOptions';
import {AccountMenu} from '../AccountMenu';
import type {NavItem} from './KiteLayout';
const pages=NAV_OPTIONS.map(([id,label])=>({id,label}));
export function MobileExperienceMenu({activeNav,onNavClick,tools=[],onSearch}:{activeNav:NavItem;onNavClick:(nav:NavItem)=>void;userName?:string;tools?:Array<{id:NavItem;label:string}>;onSearch?:()=>void}){
  const options=useExperienceOptions(s=>s.options);
  const [host,setHost]=useState<HTMLElement|null>(null);
  useEffect(()=>setHost(document.getElementById('sterling-mobile-header-actions')),[]);
  const visible=pages.filter(p=>!options.navHidden.includes(p.id)).sort((a,b)=>options.navOrder.indexOf(a.id)-options.navOrder.indexOf(b.id));
  return host?createPortal(<div className="sterling-mobile-controls"><button className="sterling-mobile-search" aria-label="Search watchlist" onClick={onSearch||(()=>window.dispatchEvent(new Event('kite-open-watchlist-search')))}>⌕</button><AccountMenu><button type="button" onClick={()=>{requestAnimationFrame(()=>useKiteStatusCardStore.getState().openCard());}}>Broker connection</button><nav aria-label="Workspace pages">{visible.map(p=><button key={p.id} aria-current={activeNav===p.id?'page':undefined} onClick={()=>onNavClick(p.id)}>{tools.find(t=>t.id===p.id)?.label||p.label}</button>)}</nav></AccountMenu></div>,host):null;
}

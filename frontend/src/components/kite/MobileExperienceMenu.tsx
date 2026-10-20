import { useMobileBackLayer } from '../../hooks/useMobileBackLayer';
import { useExperienceOptions } from '../../store/useExperienceOptions';
import React, {useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {ThemeToggle} from './ThemeToggle';
import type {NavItem} from './KiteLayout';
import {useMobileExperience} from '../../store/useMobileExperience';
import {hasUnsavedDraft} from './config/unsavedDraftGuard';
import {useAppInstall} from '../../store/useAppInstall';
const destinations:Array<[NavItem,string,string]>=[
  ['dashboard','Dashboard','Overview & balances'],['orders','Orders','Order status & history'],
  ['positions','Positions','Exposure & protection'],['holdings','Holdings','Your investments'],
  ['journal','Journal','Trades & performance'],['more','Markets & funds','Funds, alerts & tools'],
  ['connect','Settings & accounts','Kite, strategies & Sterling users'],['astro','Astrology','Market calendar'],
  ['pcr','PCR','Options sentiment'],['backtest','Backtest','Strategy research'],['data','Data','History & imports'],['help','Help','Guides & support'],
];
export function MobileExperienceMenu({activeNav,onNavClick,userName,tools=[],onSearch}:{activeNav:NavItem;onNavClick:(nav:NavItem)=>void;userName?:string;tools?:Array<{id:NavItem;label:string}>;onSearch?:()=>void}) {
  const navOptions=useExperienceOptions(s=>s.options);
  const visibleDestinations=[...destinations].filter(d=>!navOptions.navHidden.includes(d[0])).sort((a,b)=>navOptions.navOrder.indexOf(a[0])-navOptions.navOrder.indexOf(b[0]));
  const [viewTitle,setViewTitle]=useState('');
  useEffect(()=>{
    const update=(event:Event)=>setViewTitle(String((event as CustomEvent).detail || ''));
    window.addEventListener('kite-main-view-title',update);
    window.addEventListener('kite-mobile-pane-title',update);
    return()=>{window.removeEventListener('kite-main-view-title',update);window.removeEventListener('kite-mobile-pane-title',update);};
  },[]);
  const [open,setOpen]=useState(false);const [installNote,setInstallNote]=useState('');const [busy,setBusy]=useState(false);
  const {style,setStyle}=useMobileExperience();const {prompt,installed}=useAppInstall();
  const closeRef=useRef<HTMLButtonElement>(null);const triggerRef=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    if (!open) return;
    closeRef.current?.focus();
    const key=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){setOpen(false);triggerRef.current?.focus();}
      if(event.key==='Tab'){
        const items=Array.from(document.querySelectorAll<HTMLButtonElement>('.mobile-experience-sheet button:not(:disabled)'));
        const first=items[0],last=items[items.length-1];
        if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
      }
    };
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[open]);
  function chooseStyle(next:'app'|'full') {
    if(next===style)return;
    if(hasUnsavedDraft() && !window.confirm('Switch layouts and discard unsaved settings changes?'))return;
    setStyle(next);
  }
  async function install(){
    if(!prompt){setInstallNote('Android: open Chrome or Brave’s ⋮ menu → Install app / Add to Home screen. iPhone or iPad: open in Safari → Share → Add to Home Screen. Use the HTTPS app address.');return;}
    setBusy(true);
    try {await prompt.prompt();const choice=await prompt.userChoice;useAppInstall.setState({prompt:null});setInstallNote(choice.outcome==='accepted'?'Installation requested. Complete the browser confirmation.':'Installation dismissed. You can install later from the browser menu.');}
    catch {setInstallNote('Open the browser menu and choose Install app / Add to Home screen.');}
    finally{setBusy(false);}
  }
  useMobileBackLayer(open,()=>setOpen(false),'mobile-menu');
  useEffect(()=>{const close=()=>setOpen(false);window.addEventListener('sterling-home',close);return()=>window.removeEventListener('sterling-home',close);},[]);
  const headerHost=document.getElementById('sterling-mobile-header-actions');
  return <>
    {headerHost && createPortal(<div className="sterling-mobile-controls">
      <button className="sterling-mobile-search" type="button" aria-label="Search watchlist"
        title="Search watchlist" onClick={onSearch || (()=>window.dispatchEvent(new CustomEvent('kite-open-watchlist-search')))}>
        <span aria-hidden="true">⌕</span>
      </button>
      <button ref={triggerRef} className="sterling-mobile-menu-trigger" type="button"
        aria-label="Menu" aria-haspopup="dialog" aria-expanded={open}
        onClick={()=>setOpen(value=>!value)}><span aria-hidden="true">☰</span></button>
    </div>,headerHost)}
    {open && createPortal(<div className="mobile-experience-backdrop" onClick={event=>{if(event.target===event.currentTarget)setOpen(false);}}>
      <section className="mobile-experience-sheet" role="dialog" aria-modal="true" aria-label="Sterling mobile menu">
        <header><div><h2>Your Sterling</h2><p>Everything in one place</p></div><div className="mobile-sheet-actions"><ThemeToggle size={40} /><button ref={closeRef} aria-label="Close mobile menu" onClick={()=>{setOpen(false);triggerRef.current?.focus();}}>✕</button></div></header>
        <div className="mobile-style-switch" role="group" aria-label="Mobile layout style">
          <button aria-pressed={style==='app'} onClick={()=>chooseStyle('app')}>Mobile app style</button>
          <button aria-pressed={style==='full'} onClick={()=>chooseStyle('full')}>Full app style</button>
        </div>
        <button onClick={()=>{setOpen(false);window.dispatchEvent(new Event('sterling-open-profile'));}}>Profile &amp; security{userName?' · '+userName:''}</button>
        <p className="mobile-menu-note">{style==='app'?'One screen at a time, with navigation at your fingertips.':'All workspace docks together. Swipe horizontally to explore.'}</p>
        {!installed && <button className="mobile-install-button" disabled={busy} onClick={install}>{busy?'Opening install…':prompt?'Install Sterling':'Install / add to home screen'}</button>}
        {installNote && <p role="status" className="mobile-menu-note">{installNote}</p>}
        {tools.length > 0 && <nav aria-label="Tools"><h3>Tools</h3>{tools.map(item => <button key={item.id} onClick={()=>{onNavClick(item.id);setOpen(false);triggerRef.current?.focus();}}>{item.label}</button>)}</nav>}
        <nav aria-label="All app pages">{visibleDestinations.map(([id,label,detail])=><button key={id} aria-current={activeNav===id?'page':undefined} onClick={()=>{onNavClick(id);setOpen(false);triggerRef.current?.focus();}}><span>{label}<small>{detail}</small></span><span aria-hidden="true">›</span></button>)}</nav>
      </section>
    </div>,document.body)}
  </>;
}

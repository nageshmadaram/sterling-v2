/**
 * The app-level light/dark switch.
 *
 * One control, one job. The status bar's three-way DK/GR/LT picker still
 * exists for the grey shell, but the thing a user reaches for a dozen times a
 * day should not make them think about a third option.
 *
 * The icon shows the theme you will GET, not the one you are in — a sun means
 * "click for light". That reads correctly whether or not you can see the
 * current state, which matters on a terminal that is mostly dark chrome.
 */
import React from 'react';
import { useTheme, useSetTheme } from '../../store/useStore';
import {createPortal} from 'react-dom';
import {useAppearance} from '../../appearance/store';
import {THEME_PRESETS} from '../../appearance/catalog';
import {useMobileBackLayer} from '../../hooks/useMobileBackLayer';
import {toLayoutPx} from '../../styles/applyViewportScale';

function SunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.4v2.4M12 19.2v2.4M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M2.4 12h2.4M19.2 12h2.4M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20.5 14.6A8.6 8.6 0 0 1 9.4 3.5a8.6 8.6 0 1 0 11.1 11.1Z" />
    </svg>
  );
}

export function ThemeToggle({ size = 32 }: { size?: number }) {
  const theme = useTheme();
  const {settings,update} = useAppearance();
  const setTheme = useSetTheme();
  const [open,setOpen]=React.useState(false);
  const anchor=React.useRef<HTMLButtonElement>(null);
  const panel=React.useRef<HTMLDivElement>(null);
  const [position,setPosition]=React.useState({top:52,right:12});
  useMobileBackLayer(open,()=>setOpen(false),'theme-menu');
  React.useEffect(()=>{
    if(!open)return;
    const close=(e:MouseEvent)=>{if(!anchor.current?.contains(e.target as Node)&&!panel.current?.contains(e.target as Node))setOpen(false);};
    const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);anchor.current?.focus();}};
    document.addEventListener('mousedown',close);document.addEventListener('keydown',key);
    return()=>{document.removeEventListener('mousedown',close);document.removeEventListener('keydown',key);};
  },[open]);
  const choose=(preset:string,base:'light'|'dark')=>{update({preset,base,accent:''});setTheme(base);setOpen(false);anchor.current?.focus();};
  return <><button ref={anchor} type="button" aria-label="Choose theme" title="Choose theme" aria-haspopup="menu" aria-expanded={open} className="sk-theme-toggle"
    onClick={()=>{const rect=anchor.current!.getBoundingClientRect();setPosition({top:toLayoutPx(rect.bottom+6),right:toLayoutPx(window.innerWidth-rect.right)});setOpen(!open);}}
    style={{width:size,height:size,border:0,background:'none',color:'var(--k-text)',display:'inline-flex',alignItems:'center',justifyContent:'center',cursor:'pointer'}}>
    {theme==='light'?<MoonIcon/>:<SunIcon/>}</button>
    {open&&createPortal(<div ref={panel} role="menu" aria-label="Themes" className="sterling-theme-menu" style={{position:'fixed',...position,zIndex:100120}}>
      <button role="menuitemradio" aria-checked={!settings.preset&&theme==='light'} onClick={()=>choose('','light')}>☀ Light</button>
      <button role="menuitemradio" aria-checked={!settings.preset&&theme==='dark'} onClick={()=>choose('','dark')}>☾ Dark</button>
      <small>Favourites</small>
      {THEME_PRESETS.filter(p=>settings.favorites.includes(p.id)).map(p=><button key={p.id} role="menuitemradio" aria-checked={settings.preset===p.id} onClick={()=>choose(p.id,p.dark?'dark':'light')}><span style={{color:p.accent}}>●</span> {p.name}</button>)}
      {!settings.favorites.length&&<p>Save favourite themes in Config → Experience.</p>}
    </div>,document.body)}</>;
}

import {useLayoutEffect,useRef,type RefObject} from 'react';
const layers:symbol[]=[];
let restoreRoots:Array<HTMLElement>=[];
let overflow='';
function visible(element:HTMLElement){
  for(let node:HTMLElement|null=element;node;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden')return false;}
  return true;
}
const focusable='button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]';
/** Shared focus, scroll and background behavior for portalled application dialogs. */
export function useModalLayer(open:boolean,panel:RefObject<HTMLElement|null>,onClose:()=>void){
  const close=useRef(onClose);close.current=onClose;
  useLayoutEffect(()=>{
    if(!open)return;
    const id=Symbol('modal'),previous=document.activeElement as HTMLElement|null;
    if(!layers.length){
      overflow=document.body.style.overflow;document.body.style.overflow='hidden';
      restoreRoots=[...document.querySelectorAll<HTMLElement>('.workspace-shell,.sterling-signin,.term-root')].filter(root=>!root.hasAttribute('inert')&&!root.parentElement?.closest('.workspace-shell,.sterling-signin'));
      restoreRoots.forEach(root=>root.setAttribute('inert',''));
    }
    layers.push(id);
    const controls=()=>[...(panel.current?.querySelectorAll<HTMLElement>(focusable)||[])].filter(el=>!el.closest('[hidden],details:not([open]) :not(summary)')&&visible(el));
    (controls()[0]||panel.current)?.focus();
    const key=(event:KeyboardEvent)=>{
      if(layers.at(-1)!==id)return;
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close.current();}
      if(event.key==='Tab'){
        const items=controls(),first=items[0],last=items.at(-1);
        if(!first){event.preventDefault();panel.current?.focus();return;}
        if(!panel.current?.contains(document.activeElement)||(event.shiftKey?document.activeElement===first:document.activeElement===last)){
          event.preventDefault();(event.shiftKey?last:first)?.focus();
        }
      }
    };
    document.addEventListener('keydown',key,true);
    return()=>{
      document.removeEventListener('keydown',key,true);layers.splice(layers.indexOf(id),1);
      if(!layers.length){restoreRoots.forEach(root=>root.removeAttribute('inert'));restoreRoots=[];document.body.style.overflow=overflow;}
      if(previous?.isConnected&&!previous.closest('[inert]'))previous.focus();
      else if(!layers.length)document.querySelector<HTMLElement>('.account-menu-trigger')?.focus();
    };
  },[open,panel]);
}

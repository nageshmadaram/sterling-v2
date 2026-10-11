import {useEffect,useRef,useState} from 'react';
import {hasUnsavedDraft} from './config/unsavedDraftGuard';
export const REFRESH_DISTANCE=72;
const reloadPage=()=>window.location.reload();
export function MobilePullToRefresh({onRefresh=reloadPage}:{onRefresh?:()=>void}) {
 const ref=useRef<HTMLDivElement>(null);const [distance,setDistance]=useState(0);const [note,setNote]=useState('');
 useEffect(()=>{
  const host=ref.current?.parentElement;if(!host)return;
  let startY:number|null=null;let startX=0;let current=0;
  const reset=()=>{startY=null;current=0;setDistance(0);};
  const start=(event:TouchEvent)=>{
   reset();if(event.touches.length!==1)return;
   const target=event.target as HTMLElement;
   if(target.closest('input,textarea,select,button,[role="dialog"],canvas,[aria-label$="chart workspace"]'))return;
   let node:HTMLElement|null=target;
   while(node && node!==host){if(node.scrollTop>0)return;node=node.parentElement;}
   startY=event.touches[0].clientY;startX=event.touches[0].clientX;setNote('');
  };
  const move=(event:TouchEvent)=>{
   if(startY===null || event.touches.length!==1)return;
   const touch=event.touches[0];const dy=touch.clientY-startY;
   if(Math.abs(touch.clientX-startX)>Math.max(24,Math.abs(dy))){reset();return;}
   current=Math.max(0,Math.min(110,dy*.55));setDistance(current);
   if(current>0 && event.cancelable)event.preventDefault();
  };
  const end=()=>{
   const ready=current>=REFRESH_DISTANCE;reset();
   if(!ready)return;
   if(hasUnsavedDraft()){setNote('Save or discard your changes before refreshing.');return;}
   setNote('Refreshing…');onRefresh();
  };
  host.addEventListener('touchstart',start,{passive:true});host.addEventListener('touchmove',move,{passive:false});host.addEventListener('touchend',end);host.addEventListener('touchcancel',reset);
  return()=>{host.removeEventListener('touchstart',start);host.removeEventListener('touchmove',move);host.removeEventListener('touchend',end);host.removeEventListener('touchcancel',reset);};
 },[onRefresh]);
 return <div ref={ref} className="mobile-refresh-indicator" role="status" aria-hidden={distance===0 && !note} style={{opacity:distance>0||note?1:0,transform:`translate(-50%, ${Math.max(0,distance-24)}px)`}}><span aria-hidden style={{transform:`rotate(${distance*4}deg)`}}>↻</span>{note || (distance>=REFRESH_DISTANCE?'Release to refresh':'Pull to refresh')}</div>;
}

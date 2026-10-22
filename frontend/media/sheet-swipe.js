/* Swipe down to close, for every overlay in Radio and TV (sheets, Now Playing, dialogs).
   One delegated touch handler: the panel follows the finger (transform only, one write per
   frame) when the drag starts at the top of whatever is scrolling, and closes past a distance
   or on a fast flick; otherwise it springs back. Horizontal drags and form controls are left
   alone, so rows, sliders and inputs keep working. Apps register a selector and a close. */
(()=>{
 const entries=[{selector:'dialog[open]',close:el=>el.close()}];
 const register=(selector,close)=>{entries.push({selector,close});};
 let g=null,raf=0;
 // True when the touch began inside something already scrolled down: that drag scrolls back up.
 const scrolled=(node,panel)=>{for(let n=node;n;n=n.parentElement){if(n.scrollTop>0)return true;if(n===panel)return false;}return false;};
 const paint=()=>{raf=0;if(g?.on)g.panel.style.transform=`translate3d(0,${Math.max(0,g.dy)}px,0)`;};
 const reset=panel=>{panel.style.transition='';panel.style.transform='';panel.style.willChange='';};
 document.addEventListener('touchstart',e=>{
  g=null;if(e.touches.length!==1)return;const t=e.target;
  if(!(t instanceof Element)||t.closest('input,select,textarea,[data-no-swipe]'))return;
  for(let i=entries.length-1;i>=0;i--){const panel=t.closest(entries[i].selector);if(!panel)continue;
   if(!scrolled(t,panel))g={panel,close:entries[i].close,x:e.touches[0].clientX,y:e.touches[0].clientY,t:performance.now(),dy:0,on:false};return;}
 },{passive:true,capture:true});
 document.addEventListener('touchmove',e=>{
  if(!g)return;const dy=e.touches[0].clientY-g.y,dx=e.touches[0].clientX-g.x;
  if(!g.on){if(Math.abs(dy)<10&&Math.abs(dx)<10)return;if(dy<=0||Math.abs(dx)>dy){g=null;return;}
   g.on=true;g.t=performance.now();g.y=e.touches[0].clientY;g.panel.style.transition='none';g.panel.style.willChange='transform';}
  if(e.cancelable)e.preventDefault();g.dy=e.touches[0].clientY-g.y;if(!raf)raf=requestAnimationFrame(paint);
 },{passive:false,capture:true});
 const end=()=>{
  const s=g;g=null;if(!s?.on)return;const flick=s.dy/Math.max(1,performance.now()-s.t)>0.55&&s.dy>36;
  s.panel.style.transition='transform .2s cubic-bezier(.2,.8,.2,1)';
  if(s.dy>110||flick){s.panel.style.transform='translate3d(0,100%,0)';setTimeout(()=>{try{s.close(s.panel);}finally{reset(s.panel);}},190);}
  else{s.panel.style.transform='translate3d(0,0,0)';setTimeout(()=>reset(s.panel),210);}
 };
 document.addEventListener('touchend',end,{passive:true,capture:true});
 document.addEventListener('touchcancel',end,{passive:true,capture:true});
 window.SterlingSheetSwipe=Object.freeze({register});
})();

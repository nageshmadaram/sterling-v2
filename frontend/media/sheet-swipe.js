/* One gesture owner for native settings sheets and the shared media players.
   A grabber/header uses pointer capture, including mouse; content keeps normal
   scrolling and starts a dismiss gesture only when its scroll position is zero. */
(()=>{
 const entries=[{selector:'dialog[open]',close:panel=>panel.close()}];
 const register=(selector,close)=>entries.push({selector,close});
 let gesture=null,raf=0;
 const scrolled=(node,panel)=>{for(let n=node;n;n=n.parentElement){if(n.scrollTop>0)return true;if(n===panel)break;}return false;};
 const match=target=>{for(let i=entries.length-1;i>=0;i--){const panel=target.closest(entries[i].selector);if(panel)return {panel,close:entries[i].close};}return null;};
 const reset=panel=>{panel.style.transition='';panel.style.transform='';panel.style.willChange='';};
 const paint=()=>{raf=0;if(gesture?.on)gesture.panel.style.transform=`translate3d(0,${Math.max(-Math.max(0,gesture.top-12),gesture.dy)}px,0)`;};
 function begin(target,x,y,pointer){
  if(!(target instanceof Element)||target.closest('input,select,textarea,button,a,[data-no-swipe]'))return;
  const owner=match(target);if(!owner||scrolled(target,owner.panel))return;
  gesture={...owner,x,y,dy:0,t:performance.now(),top:owner.panel.getBoundingClientRect().top,on:false,pointer};
 }
 function move(x,y,event){
  const g=gesture;if(!g)return;const dy=y-g.y,dx=x-g.x;
  if(!g.on){if(Math.abs(dy)<8&&Math.abs(dx)<8)return;if(Math.abs(dx)>Math.abs(dy)||(g.pointer==null&&dy<0)){gesture=null;return;}
   g.on=true;g.panel.style.transition='none';g.panel.style.willChange='transform';}
  if(event.cancelable)event.preventDefault();g.dy=dy;if(!raf)raf=requestAnimationFrame(paint);
 }
 function end(cancelled=false){
  const g=gesture;gesture=null;if(!g)return;
  if(raf){cancelAnimationFrame(raf);raf=0;}
  if(!g.on){reset(g.panel);return;}
  const dismiss=!cancelled&&(g.dy>90||(g.dy>36&&g.dy/Math.max(1,performance.now()-g.t)>.55));
  if(dismiss){try{g.close(g.panel);}finally{reset(g.panel);}}
  else{if(!cancelled&&g.pointer&&g.dy<-55&&g.panel.matches('dialog'))g.panel.dataset.sheetExpanded='true';reset(g.panel);}
 }
 document.addEventListener('pointerdown',event=>{
  if(!(event.target instanceof Element)||!event.target.closest('.media-sheet-handle,.media-comfort-handle,dialog>header,.media-sheet-content>div:first-child'))return;
  begin(event.target,event.clientX,event.clientY,event.pointerId);
  if(gesture)gesture.panel.setPointerCapture?.(event.pointerId);
 },true);
 document.addEventListener('pointermove',event=>{if(gesture?.pointer===event.pointerId)move(event.clientX,event.clientY,event);},{capture:true,passive:false});
 document.addEventListener('pointerup',event=>{if(gesture?.pointer===event.pointerId)end();},true);
 document.addEventListener('pointercancel',event=>{if(gesture?.pointer===event.pointerId)end(true);},true);
 document.addEventListener('touchstart',event=>{
  if(gesture?.pointer!=null||event.touches.length!==1)return;
  begin(event.target,event.touches[0].clientX,event.touches[0].clientY,null);
 },{passive:true,capture:true});
 document.addEventListener('touchmove',event=>{if(gesture&&gesture.pointer==null&&event.touches.length===1)move(event.touches[0].clientX,event.touches[0].clientY,event);},{passive:false,capture:true});
 document.addEventListener('touchend',()=>{if(gesture?.pointer==null)end();},{passive:true,capture:true});
 document.addEventListener('touchcancel',()=>{if(gesture?.pointer==null)end(true);},{passive:true,capture:true});
 const prepared=new WeakSet();
 function prepare(panel){
  if(prepared.has(panel))return;prepared.add(panel);
  if(!panel.querySelector(':scope>.media-comfort-handle,:scope>.media-sheet-handle')){const handle=document.createElement('div');handle.className='media-sheet-handle';handle.setAttribute('aria-hidden','true');panel.prepend(handle);}
  panel.addEventListener('close',()=>{delete panel.dataset.sheetExpanded;reset(panel);});
 }
 function scan(root){if(root instanceof Element&&root.matches('dialog'))prepare(root);root.querySelectorAll?.('dialog').forEach(prepare);}
 const mount=()=>{scan(document);new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node instanceof Element)scan(node);}).observe(document.body,{subtree:true,childList:true});};
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
 window.SterlingSheetSwipe=Object.freeze({register});
})();

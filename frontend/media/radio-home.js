/* Radio home, Spotify style: rows of horizontal shelves instead of one long grid. Each row renders 12
   cards and adds 12 more only when scrolled near its right edge, so the page never builds thousands of
   cards. A row is rebuilt only when its content changes. */
(()=>{
 const BATCH=12,CAP=300;
 function mount({container,artwork,onPlay,fallback}){
  const doc=document,root=doc.createElement('div');root.id='radio-home-shelves';container.append(root);
  const rows=new Map();
  // Generated station badges cost ~8 ms each (canvas to image). Draw them only for cards near the
  // screen, a few per frame, so a chip tap paints at once instead of after a second of drawing.
  const pending=[];let drawing=0;
  const drawSome=()=>{drawing=0;const end=performance.now()+8;while(pending.length&&performance.now()<end){const [img,station]=pending.shift();if(img.isConnected&&!img.src)img.src=artwork(station);}if(pending.length)drawing=requestAnimationFrame(drawSome);};
  const art=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){art.unobserve(entry.target);const station=entry.target._station;if(station){pending.push([entry.target,station]);if(!drawing)drawing=requestAnimationFrame(drawSome);}}},{root:null,rootMargin:'200px 400px'});
  const grow=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)rows.get(entry.target.dataset.row)?.more();},{root:null,rootMargin:'0px 600px 0px 0px'});
  function card(item,state){
   const b=doc.createElement(item.kind==='external'?'a':'button');if(item.kind!=='external')b.type='button';
   if(item.kind==='external'){const address=new URL(item.url);if(address.protocol!=='https:'||address.hostname!=='www.youtube.com')throw Error('Unsupported mix link');b.href=address.href;b.target='_blank';b.rel='noopener noreferrer';b.style.display='block';b.style.textDecoration='none';}
   if(item.kind==='station'){
    b.className='rh-card';b.setAttribute('aria-label',`Play ${item.station.title}`);
    const img=doc.createElement('img');img.alt='';img.decoding='async';img._station=item.station;art.observe(img);
    // A broken logo falls back to the generated station badge instead of a blank square.
    img.addEventListener('error',()=>{const next=fallback?.(item.station);if(next&&next!==img.src)img.src=next;else img.style.visibility='hidden';},{once:true});
    const strong=doc.createElement('strong'),small=doc.createElement('small');strong.textContent=item.station.title;small.textContent=item.station.freqKhz?`${item.station.freqKhz} kHz · ${item.station.country||''}`:(item.station.country||item.station.subtitle||'');
    b.append(img,strong,small);b.addEventListener('click',()=>onPlay(item.station.id,state?.def.items.filter(i=>i.kind==='station').map(i=>i.station)||[]));
   }else{
    b.className='rh-card rh-tile';b.style.setProperty('--tile',item.color||'#1e3264');b.setAttribute('aria-label',`Open ${item.title}`);
    const art=doc.createElement('span');art.className='rh-mosaic';
    for(const src of (item.art||[]).slice(0,4)){const img=doc.createElement('img');img.alt='';img.loading='lazy';img.decoding='async';img.src=src;img.addEventListener('error',()=>{img.style.visibility='hidden';},{once:true});art.append(img);}
    // Initials sit behind the artwork, so a tile whose logos fail to load still reads clearly.
    const big=doc.createElement('b');big.textContent=item.badge||item.title.split(/[\s:,&]+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase();art.prepend(big);
    const strong=doc.createElement('strong'),small=doc.createElement('small');strong.textContent=item.title;small.textContent=item.subtitle||'';
    b.append(art,strong,small);if(item.kind!=='external')b.addEventListener('click',()=>item.open());
   }
   return b;
  }
  function row(def){
   const section=doc.createElement('section');section.className='rh-row'+(def.size==='large'?' rh-row-large':'');section.dataset.row=def.id;
   const head=doc.createElement('header'),h2=doc.createElement('h2');h2.textContent=def.title;head.append(h2);
   if(def.open){const all=doc.createElement('button');all.type='button';all.className='rh-all';all.textContent='Show all';all.addEventListener('click',def.open);head.append(all);}
   const strip=doc.createElement('div');strip.className='rh-strip';const end=doc.createElement('span');end.className='rh-end';end.dataset.row=def.id;
   section.append(head,strip);let shown=0;
   const state={def,section,more(){const next=state.def.items.slice(shown,Math.min(shown+BATCH,CAP));if(!next.length)return;end.remove();strip.append(...next.map(item=>card(item,state)));shown+=next.length;if(shown<Math.min(state.def.items.length,CAP))strip.append(end);}};
   grow.observe(end);state.more();return state;
  }
  function render(defs){
   const seen=new Set();let previous=null;
   for(const def of defs){
    if(!def.items.length)continue;seen.add(def.id);
    const signature=def.title+'|'+(def.size||'')+'|'+def.items.length+'|'+def.items.slice(0,BATCH).map(i=>i.key).join(',');
    let state=rows.get(def.id);
    if(!state||state.signature!==signature){const fresh=row(def);fresh.signature=signature;if(state)state.section.replaceWith(fresh.section);state=fresh;rows.set(def.id,state);}
    else state.def=def;
    if(previous?previous.nextSibling!==state.section:root.firstChild!==state.section)(previous?previous.after(state.section):root.prepend(state.section));
    previous=state.section;
   }
   for(const [id,state] of rows)if(!seen.has(id)){state.section.remove();rows.delete(id);}
  }
  return {render,root};
 }
 window.SterlingRadioHome=Object.freeze({mount});
})();

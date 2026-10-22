/* Spotify-style Search, Recents and Your Library pages shared by Radio and TV.
   Each app passes an adapter; the shell owns layout only and never touches
   playback, storage or the network itself. */
(()=>{
 const ICON={
  spark:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9L12 2Zm7 12 .9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14Z"/></svg>',
  search:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 3a7.5 7.5 0 1 0 4.7 13.35l4.72 4.72 1.41-1.41-4.72-4.72A7.5 7.5 0 0 0 10.5 3Zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Z"/></svg>',
  back:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2Z"/></svg>',
  plus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4h2v7h7v2h-7v7h-2v-7H4v-2h7V4Z"/></svg>',
  add:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16Zm-1 3v4H7v2h4v4h2v-4h4v-2h-4V7h-2Z"/></svg>',
  added:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="#1ed760"/><path d="m10.4 15.6-3.5-3.5 1.4-1.4 2.1 2.1 5-5 1.4 1.4-6.4 6.4Z" fill="#000"/></svg>',
  close:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6 10.6 12 5 6.4 6.4 5Z"/></svg>',
  sort:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 4 4 4H9v8H7V8H4l4-4Zm8 16-4-4h3V8h2v8h3l-4 4Z"/></svg>',
  grid:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z"/></svg>',
  list:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v2H4V5Zm0 6h16v2H4v-2Zm0 6h16v2H4v-2Z"/></svg>',
  pin:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 3 21 8l-2 1-3 3 1 5-2 2-4-4-5 5-1-1 5-5-4-4 2-2 5 1 3-3 1-2Z" fill="#1ed760"/></svg>',
  heart:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3.1 4.5 6.9 4.5c2 0 3.6 1.1 5.1 2.9 1.5-1.8 3.1-2.9 5.1-2.9 3.8 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21Z" fill="#fff"/></svg>',
  clock:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm1 5v5.4l4 2.4-1 1.7-5-3V7h2Z" fill="#fff"/></svg>',
 };
 const PALETTE=['#e13300','#1e3264','#8400e7','#148a08','#dc148c','#e8115b','#477d95','#7358ff','#ba5d07','#509bf5','#af2896','#27856a','#e91429','#8d67ab','#0d73ec','#d84000'];
 function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;}
 function iconButton(name,label,cls='sps-icon'){const b=el('button',cls);b.type='button';b.innerHTML=ICON[name];b.setAttribute('aria-label',label);b.title=label;return b;}
 function art(src,cls='sps-art',fallback){const img=el('img',cls);img.alt='';img.loading='lazy';img.decoding='async';if(src)img.src=src;else if(fallback)img.src=fallback()||'';
  // A broken logo falls back to the generated station badge instead of leaving a hole.
  img.addEventListener('error',()=>{const next=fallback?.();if(next&&next!==img.src){img.src=next;fallback=null;}else img.style.visibility='hidden';});return img;}

 function mount(adapter){
  const a={noun:'stations',placeholder:'What do you want to listen to?',libraryChips:[],...adapter};
  const count=n=>{n=Number(n)||0;return `${n.toLocaleString()} ${n===1?a.noun.replace(/s$/,''):a.noun}`;};
  const root=el('div','sps-root');root.hidden=true;root.dataset.kind=a.kind||'media';document.body.append(root);
  let page=null,focus=false,query='',chip='',sort='recents',grid=false,drill=null,timer=0,lastResults='';
  const rowCache=new Map();
  const state=()=>({page,focus,query});

  function header(title){
   const bar=el('header','sps-header');const avatar=el('button','sps-avatar',a.avatar?.()||'S');avatar.type='button';avatar.setAttribute('aria-label','Profile and settings');
   avatar.addEventListener('click',()=>a.openSettings?.());
   bar.append(avatar,el('h1','',title));
   if(a.openExtras){const extras=iconButton('spark','Extras: sleep timer, alarm, stats, backup','sps-icon sps-extras');extras.addEventListener('click',()=>a.openExtras());bar.append(extras);}
   return bar;
  }
  function row(item,{actions=true,removable=false}={}){
   const r=el('div','sps-row');r.setAttribute('role','button');r.tabIndex=0;r.dataset.id=item.id;
   const body=el('div','sps-row-body');body.append(el('strong','',item.title),el('small','',[item.type||a.itemType||'Station',item.subtitle].filter(Boolean).join(' • ')));
   r.append(art(item.art,'sps-art',a.fallbackArt?()=>a.fallbackArt(item.id):null),body);
   if(actions){const add=iconButton('add','','sps-row-action');
    // Update the icon in place: a full render would rebuild the list (and the search box) and flicker.
    const paint=()=>{const fav=Boolean(a.isFavorite?.(item.id));add.innerHTML=ICON[fav?'added':'add'];const label=fav?`Remove ${item.title} from your library`:`Add ${item.title} to your library`;add.setAttribute('aria-label',label);add.title=label;};
    r._paint=paint;paint();add.addEventListener('click',e=>{e.stopPropagation();a.toggleFavorite?.(item.id);paint();if(!(page==='search'&&focus))render();});r.append(add);}
   if(removable){const x=iconButton('close',`Remove ${item.title} from recents`,'sps-row-action');x.addEventListener('click',e=>{e.stopPropagation();a.removeRecent?.(item.id);if(page==='search'&&focus){r.remove();rowCache.delete('r:'+item.id);lastResults='';}else render();});r.append(x);}
   // The list this row sits in becomes the queue, in the order shown (Next continues after it).
   const play=()=>{const ids=[...(r.parentElement?.querySelectorAll(':scope>.sps-row[data-id]')||[])].map(n=>n.dataset.id);a.play(item.id,ids.length>1?ids:undefined);};r.addEventListener('click',play);r.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();play();}});
   return r;
  }

  function renderSearch(){
   if(focus){
    const top=el('div','sps-search-top');const back=iconButton('back','Back to search');back.addEventListener('click',()=>{focus=false;query='';render();});
    const input=el('input','sps-search-field');input.type='search';input.placeholder=a.placeholder;input.value=query;input.setAttribute('aria-label',a.placeholder);
    input.addEventListener('input',()=>{query=input.value;clearTimeout(timer);if(!query.trim())renderResults(list);else timer=setTimeout(()=>renderResults(list),70);});
    input.addEventListener('keydown',e=>{if(e.key==='Escape'){focus=false;query='';render();}if(e.key==='Enter'){const first=list.querySelector('.sps-row');first?.click();}});
    top.append(back,input);const list=el('div','sps-list');root.append(top,list);lastResults='';renderResults(list);
    requestAnimationFrame(()=>input.focus({preventScroll:true}));return;
   }
   root.append(header('Search'));
   const box=el('button','sps-search-box');box.type='button';box.innerHTML=ICON.search;box.append(el('span','',a.placeholder));box.addEventListener('click',()=>{focus=true;render();});root.append(box);
   const tiles=el('div','sps-tiles');
   (a.categories?.()||[]).forEach((cat,i)=>{const t=el('button','sps-tile');t.type='button';t.style.background=cat.color||PALETTE[i%PALETTE.length];
    t.append(el('strong','',cat.label),el('small','',count(cat.count)));if(cat.art)t.append(art(cat.art,'sps-tile-art'));
    t.addEventListener('click',()=>{hide();a.openCategory?.(cat.key);});tiles.append(t);});
   root.append(tiles);
   const feature=a.featured?.()||[];
   if(feature.length){root.append(el('h2','sps-section',a.featuredTitle||'Discover something new'));const strip=el('div','sps-tall');
    for(const item of feature.slice(0,10)){const card=el('button','sps-tall-card');card.type='button';card.append(art(item.art),el('strong','',item.title));card.addEventListener('click',()=>a.play(item.id));strip.append(card);}root.append(strip);}
  }
  // Rows are reused by id, so typing only adds or removes rows; loaded artwork never reloads.
  function cachedRow(item,removable){
   const key=`${removable?'r':'s'}:${item.id}`;let node=rowCache.get(key);
   if(!node){node=row(item,{removable});rowCache.set(key,node);if(rowCache.size>400)rowCache.delete(rowCache.keys().next().value);}else node._paint?.();
   return node;
  }
  function find(q,limit){
   if(a.search)return a.search(q,limit);
   const out=[];for(const item of a.items()){if(`${item.title} ${item.subtitle||''}`.toLowerCase().includes(q)){out.push(item);if(out.length>=limit)break;}}return out;
  }
  function renderResults(list){
   if(!query.trim()){
    const recents=(a.recents?.()||[]).slice(0,30),signature='recents|'+recents.map(item=>item.id).join('|');
    if(signature===lastResults&&list.childElementCount)return;lastResults=signature;
    list.replaceChildren(el('h2','sps-section',recents.length?'Recents':'No recent searches'),...recents.map(item=>cachedRow(item,true)));root.scrollTop=0;return;
   }
   const q=query.trim().toLowerCase(),out=find(q,60),signature='q|'+out.map(item=>item.id).join('|');
   if(signature===lastResults&&list.childElementCount)return;lastResults=signature;
   if(!out.length){list.replaceChildren(el('p','sps-empty',`No ${a.noun} found for “${query.trim()}”.`));return;}
   list.replaceChildren(...out.map(item=>cachedRow(item,false)));root.scrollTop=0;
  }

  function libraryEntries(){
   const favs=a.favorites?.()||[],recents=a.recents?.()||[];
   const pinned=[{id:'__liked',title:a.likedTitle||'Liked',subtitle:`Playlist • ${count(favs.length)}`,tile:'liked',pinned:true,items:favs},
    {id:'__recent',title:'Recently played',subtitle:`Updated ${recents.length?'today':'when you play'}`,tile:'recent',pinned:true,items:recents}];
   const groups=(a.categories?.()||[]).filter(c=>c.count>0).map(c=>({id:'cat:'+c.key,title:c.label,subtitle:`Playlist • ${count(c.count)}`,mosaic:c.mosaic||[c.art].filter(Boolean),category:c.key,chip:c.chip||'playlists'}));
   let entries=[...pinned,...groups.map(g=>({...g})),...favs.map(f=>({...f,subtitle:[f.type||a.itemType||'Station',f.subtitle].filter(Boolean).join(' • '),chip:'saved'}))];
   if(chip)entries=entries.filter(e=>e.pinned?chip==='playlists':e.chip===chip);
   if(sort==='alpha')entries=[...entries.filter(e=>e.pinned),...entries.filter(e=>!e.pinned).sort((x,y)=>x.title.localeCompare(y.title))];
   return entries;
  }
  function tile(entry){
   if(entry.tile==='liked'){const t=el('span','sps-art sps-art-liked');t.innerHTML=ICON.heart;return t;}
   if(entry.tile==='recent'){const t=el('span','sps-art sps-art-recent');t.innerHTML=ICON.clock;return t;}
   if(entry.mosaic&&entry.mosaic.length>1){const m=el('span','sps-art sps-mosaic');for(const src of entry.mosaic.slice(0,4))m.append(art(src,''));return m;}
   return art(entry.mosaic?.[0]||entry.art);
  }
  function renderLibrary(){
   if(drill){
    const top=el('div','sps-search-top');const back=iconButton('back','Back to your library');back.addEventListener('click',()=>{drill=null;render();});
    top.append(back,el('h1','sps-drill-title',drill.title));root.append(top);const list=el('div','sps-list');
    const items=drill.items||[];if(!items.length)list.append(el('p','sps-empty',drill.id==='__liked'?`Tap ⊕ on any ${a.itemType?.toLowerCase()||'station'} to save it here.`:'Nothing played yet.'));
    for(const item of items)list.append(row(item,{removable:drill.id==='__recent'}));root.append(list);return;
   }
   const head=header('Your Library');const tools=el('div','sps-header-tools');const find=iconButton('search','Search your library');find.addEventListener('click',()=>{page='search';focus=true;render();});
   const plus=iconButton('plus',a.createLabel||'Add a source');plus.addEventListener('click',()=>a.create?.());tools.append(find,plus);head.append(tools);root.append(head);
   const chips=el('div','sps-chips');for(const c of a.libraryChips){const b=el('button','sps-chip',c.label);b.type='button';b.setAttribute('aria-pressed',String(chip===c.key));b.addEventListener('click',()=>{chip=chip===c.key?'':c.key;render();});chips.append(b);}root.append(chips);
   const bar=el('div','sps-sortbar');const s=el('button','sps-sort');s.type='button';s.innerHTML=ICON.sort;s.append(el('span','',sort==='recents'?'Recents':'Alphabetical'));s.addEventListener('click',()=>{sort=sort==='recents'?'alpha':'recents';render();});
   const g=iconButton(grid?'list':'grid',grid?'Show as list':'Show as grid');g.addEventListener('click',()=>{grid=!grid;render();});bar.append(s,g);root.append(bar);
   const list=el('div',grid?'sps-grid':'sps-list');
   for(const entry of libraryEntries()){
    const r=el('div','sps-row sps-lib-row');r.setAttribute('role','button');r.tabIndex=0;const body=el('div','sps-row-body');const title=el('strong',entry.id==='__liked'?'sps-green':'',entry.title);const sub=el('small','');
    if(entry.pinned){const p=el('span','sps-pin');p.innerHTML=ICON.pin;sub.append(p);}sub.append(document.createTextNode(entry.subtitle||''));body.append(title,sub);r.append(tile(entry),body);
    const open=()=>{if(entry.items){drill=entry;render();}else if(entry.category){hide();a.openCategory?.(entry.category);}else a.play(entry.id);};
    r.addEventListener('click',open);r.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});list.append(r);
   }
   root.append(list);
  }
  function render(){
   root.replaceChildren();root.scrollTop=0;
   if(!page){root.hidden=true;document.documentElement.classList.remove('sps-open');return;}
   root.hidden=false;document.documentElement.classList.add('sps-open');root.dataset.page=page;
   if(page==='search')renderSearch();else renderLibrary();
  }
  function show(next,opts={}){page=next;focus=Boolean(opts.focus);if(next!=='library')drill=null;render();a.onShow?.(next);}
  function hide(){if(!page)return;page=null;focus=false;query='';drill=null;render();a.onShow?.(null);}
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&page&&!focus)hide();});
  return Object.freeze({show,hide,refresh:()=>{if(page&&!(page==='search'&&focus))render();},state});
 }
 /** Dominant colour of an image, darkened for a Spotify-style backdrop. Null when unreadable. */
 function tint(img){
  try{
   if(!img||!img.complete||!img.naturalWidth)return null;
   const c=document.createElement('canvas');c.width=c.height=16;const g=c.getContext('2d',{willReadFrequently:true});g.drawImage(img,0,0,16,16);
   const d=g.getImageData(0,0,16,16).data;let r=0,gr=0,b=0,w=0;
   for(let i=0;i<d.length;i+=4){const mx=Math.max(d[i],d[i+1],d[i+2]),mn=Math.min(d[i],d[i+1],d[i+2]);const sat=mx-mn+8;r+=d[i]*sat;gr+=d[i+1]*sat;b+=d[i+2]*sat;w+=sat;}
   if(!w)return null;const k=0.78;return `rgb(${Math.round(r/w*k)},${Math.round(gr/w*k)},${Math.round(b/w*k)})`;
  }catch{return null;}
 }
 /** Paint a now-playing sheet (and optional mini player) from its artwork image. */
 function bindTint(img,targets){
  const apply=()=>{const col=tint(img);for(const [el,kind] of targets){if(!el)continue;
   if(!col){el.style.removeProperty('background');continue;}
   el.style.setProperty('background',kind==='mini'?col:`linear-gradient(180deg,${col} 0%,${col} 22%,#121212 82%)`,'important');}};
  img.addEventListener('load',apply);if(img.complete)apply();
 }
 // Pull down at the top of a scrolling page to refresh, like the Spotify app. The page scrolls inside its
 // own element, so the browser's built-in pull-to-refresh never fires there.
 function pullToRefresh(scroller,onRefresh,threshold=64){
  if(!scroller||scroller.dataset.spsPull)return;scroller.dataset.spsPull='1';
  const ring=el('div','sps-pull');ring.setAttribute('aria-hidden','true');ring.append(el('span'));document.body.append(ring);
  let start=null,pull=0,busy=false;
  const place=(y,animate)=>{ring.classList.toggle('sps-pull-animate',animate);ring.style.setProperty('--sps-pull',`${y}px`);ring.style.opacity=y>4?String(Math.min(1,y/threshold)):'0';};
  scroller.addEventListener('touchstart',e=>{start=!busy&&scroller.scrollTop<=0&&e.touches.length===1?e.touches[0].clientY:null;pull=0;},{passive:true});
  scroller.addEventListener('touchmove',e=>{if(start===null)return;const d=e.touches[0].clientY-start;if(d<=0||scroller.scrollTop>0){place(0,false);pull=0;return;}pull=Math.min(d*.5,threshold*1.4);place(pull,false);ring.classList.toggle('sps-pull-ready',pull>=threshold);},{passive:true});
  scroller.addEventListener('touchend',async()=>{if(start===null)return;start=null;
   if(pull<threshold){place(0,true);return;}
   busy=true;ring.classList.add('sps-pull-busy');place(threshold,true);
   try{await Promise.all([Promise.resolve(onRefresh()),new Promise(r=>setTimeout(r,700))]);}catch(_){}
   ring.classList.remove('sps-pull-busy','sps-pull-ready');place(0,true);busy=false;});
 }
 window.SterlingSpotifyShell=Object.freeze({mount,palette:PALETTE,tint,bindTint,pullToRefresh});
})();

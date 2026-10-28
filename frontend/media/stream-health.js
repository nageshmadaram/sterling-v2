/* Host-authenticated, database-backed catalogue checks. No bearer enters this frame. */
(()=>{
 let config,revision=0,sequence=0,pending=new Map(),review=new Map(),entries=new Map(),busy=false,lastSync=0,stats={total:0,due:0},panel;
 const request=(op,body)=>new Promise((resolve,reject)=>{
  if(parent===window)return reject(Error('Open this player in Sterling to use database checks.'));
  const requestId='health-'+(++sequence),timer=setTimeout(()=>{pending.delete(requestId);reject(Error('Stream checks are reconnecting. Try again.'));},25000);
  pending.set(requestId,{resolve,reject,timer});parent.postMessage({type:'sterling.media.health',kind:config.kind,requestId,op,body},'*');
 });
 addEventListener('message',e=>{if(e.source!==parent||e.data?.type!=='sterling.media.health.result')return;const p=pending.get(e.data.requestId);if(!p)return;clearTimeout(p.timer);pending.delete(e.data.requestId);e.data.error?p.reject(Error(e.data.error)):p.resolve(e.data.result);});
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
 const key=station=>String(config.key(station));
 const norm=s=>String(s||'').toLowerCase().replace(/\b(?:\d{3,4}p|hd|live)\b/g,'').replace(/[^\p{L}\p{N}]/gu,'');
 function catalogue(){
  const items=config.catalogue()||[],groups=new Map();entries=new Map();
  for(const station of items){const k=key(station);if(!k||k.length>2048)continue;const title=config.name(station)||'Untitled';const group=norm(title)+'|'+norm(config.context?.(station));let urls=groups.get(group);if(!urls){urls=new Set();groups.set(group,urls);}for(const url of config.sources(station)||[]){try{const u=new URL(url);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password&&url.length<=4096)urls.add(url);}catch{}}entries.set(k,{key:k,name:title.slice(0,240),group});}
  const out=[];let digest=2166136261;
  for(const entry of entries.values()){const sources=[...(groups.get(entry.group)||[])];if(!sources.length||sources.length>64)continue;const row={key:entry.key,name:entry.name,sources};out.push(row);for(const ch of row.key+sources.join('|'))digest=Math.imul(digest^ch.charCodeAt(0),16777619);}
  return {stations:out,digest:'catalogue-v1:'+out.length+':'+(digest>>>0).toString(16)};
 }
 function accept(result){const before=[...review.keys()].join('|');stats=result;review=new Map((result.review||[]).map(row=>[row.key,row]));if(before!==[...review.keys()].join('|')){revision++;config.changed?.();}render();}
 async function synchronize(force=false){
  if(busy||!config||(!force&&Date.now()-lastSync<120000))return;busy=true;
  try{
   const data=catalogue();if(!data.stations.length)return;
   const status=await request('list');accept(status);
   if(status.digest!==data.digest){
    for(let start=0;start<data.stations.length;start+=100){await request('sync',{stations:data.stations.slice(start,start+100),digest:data.digest});await new Promise(resolve=>setTimeout(resolve,100));}
    await request('sync',{stations:[],digest:data.digest,complete:true});accept(await request('list'));
   }
   lastSync=Date.now();note('Checks run gradually, every 30 days. All known sources are tried.');
  }catch(error){note(error.message);}finally{busy=false;render();}
 }
 function note(text){panel?.querySelector('[data-health-note]')?.replaceChildren(document.createTextNode(text));}
 async function action(action,key){try{accept(await request('action',{action,key}));note(action==='refresh'?'Checks queued. Playback stays available while the engine works.':'Stream list updated.');}catch(error){note(error.message);}}
 function render(){
  if(!panel?.open)return;const list=panel.querySelector('[data-health-list]'),query=panel.querySelector('input').value.toLowerCase(),mode=panel.querySelector('select').value;
  panel.querySelector('[data-health-summary]').textContent=`${stats.total||0} saved · ${review.size} need review · ${stats.due||0} checks queued`;
  list.replaceChildren();const rows=mode==='review'?[...review.values()]:[...entries.values()].filter(row=>!review.has(row.key));const visible=rows.filter(row=>row.name.toLowerCase().includes(query));
  for(const row of visible.slice(0,200)){const item=el('article'),copy=el('div');copy.append(el('strong',row.name),el('small',review.has(row.key)?row.reason:'In your available catalogue'));const toggle=el('button',review.has(row.key)?'Restore':'Move to review');toggle.type='button';toggle.onclick=()=>void action(review.has(row.key)?'restore':'review',row.key);const retry=el('button','Check');retry.type='button';retry.onclick=()=>void action('refresh',row.key);item.append(copy,retry,toggle);list.append(item);}
  if(!visible.length)list.append(el('p',mode==='review'?'No streams need review.':'No matching streams.'));
  if(visible.length>200)list.append(el('p','Showing 200 matches. Narrow the search to find a stream.'));
 }
 function open(){
  if(!panel){panel=el('dialog',null,'media-settings-dialog stream-health-dialog');panel.setAttribute('aria-label','Stream health');panel.innerHTML='<header><div><small>CATALOGUE MAINTENANCE</small><h2>Stream health</h2></div><button type="button" aria-label="Close stream health">×</button></header><p data-health-summary></p><p data-health-note>Checks run gradually, every 30 days. All known sources are tried.</p><div class="stream-health-actions"><button type="button" data-health-refresh>Check all sources</button><select aria-label="Stream list"><option value="review">Needs review</option><option value="available">Available</option></select></div><input type="search" aria-label="Find a stream" placeholder="Find a station or channel"><div data-health-list></div>';
   document.body.append(panel);panel.querySelector('[aria-label="Close stream health"]').onclick=()=>panel.close();panel.querySelector('[data-health-refresh]').onclick=async()=>{await synchronize(true);await action('refresh');};panel.querySelector('input').oninput=render;panel.querySelector('select').onchange=render;panel.addEventListener('click',e=>{if(e.target===panel)panel.close();});window.SterlingSheetSwipe?.register('.stream-health-dialog[open]',()=>panel.close());}
  panel.showModal();render();void synchronize(true);
 }
 function report(station,success){if(!config||!station)return;const k=key(station);void (async()=>{try{const known=entries.has(k);if(!known){await request('sync',{stations:[{key:k,name:config.name(station).slice(0,240),sources:config.sources(station)}]});}accept(await request('action',{action:success?'playing':'failed',key:k}));}catch(error){if(panel?.open)note(error.message);}})();}
 window.SterlingStreamHealth=Object.freeze({get revision(){return revision;},attach(options){config=options;setTimeout(()=>void synchronize(),5000);setInterval(()=>{if(!document.hidden)void synchronize();},120000);setInterval(()=>{if(panel?.open&&!busy)void request('list').then(accept).catch(error=>note(error.message));},10000);},open,report,isAvailable(station){return !config||!review.has(key(station));}});
})();

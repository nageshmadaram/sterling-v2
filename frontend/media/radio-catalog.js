/* Public radio metadata only. Catalogs stay in memory; source settings belong
   to the host's existing owner-scoped Radio configuration. */
(()=>{
 const PAGE=1000,MAX_BYTES=16*1024*1024;
 const defaults=[{id:'radio-browser',name:'Radio Browser · World directory',url:'https://de1.api.radio-browser.info/json/stations',kind:'directory',enabled:true}];
 function url(value,stream=false){
  if(typeof value!=='string'||value.length>4096||/[\x00-\x20<>"']/.test(value))return '';
  try{const u=new URL(value);return (u.protocol==='https:'||(stream&&u.protocol==='http:'))&&!u.username&&!u.password?u.href:'';}catch{return '';}
 }
 function sources(value){
  if(!Array.isArray(value))return defaults.map(row=>({...row}));
  const seen=new Set(),ids=new Set();return value.slice(0,12).flatMap((row,index)=>{
   if(!row||typeof row!=='object')return [];const address=url(row.url);
   if(!address||address.length>2048||seen.has(address))return [];seen.add(address);
   let id=typeof row.id==='string'&&/^[a-zA-Z0-9-]{1,64}$/.test(row.id)?row.id:`source-${index}`;let suffix=0;while(ids.has(id))id=`source-${index}-${++suffix}`;ids.add(id);
   return [{id,name:String(row.name||'Radio source').slice(0,80),url:address,kind:['directory','m3u','json','stream'].includes(row.kind)?row.kind:'m3u',enabled:row.enabled!==false}];
  });
 }
 const words=value=>(Array.isArray(value)?value:typeof value==='string'?value.split(','):[]).filter(v=>typeof v==='string').map(v=>v.trim().toLowerCase()).filter(Boolean).slice(0,30);
 function station(row,source,index){
  if(!row||typeof row!=='object')return null;
  const stream=url(row.url_resolved||row.streamUrl||row.url,true),name=String(row.name||row.title||'').trim().slice(0,200);
  if(!stream||!name)return null;
  const languages=words(row.language||row.languages),tags=words(row.tags||row.group),country=String(row.country||row.countrycode||'').trim().slice(0,100);
  return {id:`directory-${String(row.stationuuid||row.id||stream).slice(0,4000)}`,kind:'radio',category:languages.includes('telugu')?'telugu':languages.includes('tamil')?'tamil':'directory',title:name,
   subtitle:[country,languages.slice(0,2).join(', '),row.codec||'Live radio',stream.startsWith('http:')?'HTTP stream':''].filter(Boolean).join(' · '),
   streamUrl:stream,backupUrls:[],artwork:url(row.favicon||row.artwork||row.logo),languages,tags,country,sourceId:source.id,shortFreq:'LIVE',freqLabel:'Live radio'};
 }
 function playlist(text,source){
  if(/#EXT-X-(?:TARGETDURATION|STREAM-INF|MEDIA-SEQUENCE)/.test(text)){const item=station({name:source.name,url:source.url},source,0);return item?[item]:[];}
  const rows=[];let meta={};
  for(const raw of text.split(/\r?\n/)){
   const line=raw.trim();if(!line)continue;
   if(line.startsWith('#EXTINF:')){
    const attrs={};for(const match of line.matchAll(/([\w-]+)="([^"]*)"/g))attrs[match[1]]=match[2];
    const comma=line.search(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/);meta={name:comma>=0?line.slice(comma+1):'',favicon:attrs['tvg-logo'],group:attrs['group-title'],language:attrs['tvg-language'],country:attrs['tvg-country']};
   }else if(!line.startsWith('#')){
    try{const stream=new URL(line,source.url).href;const item=station({...meta,name:meta.name||new URL(stream).hostname,url:stream},source,rows.length);if(item)rows.push(item);}catch{}meta={};
   }
  }
  return rows;
 }
 async function read(response){
  if(!response.ok)throw Error('Source unavailable');
  if(Number(response.headers?.get('content-length'))>MAX_BYTES)throw Error('Source exceeds 16 MB. Use a paged directory URL.');
  if(!response.body?.getReader){const text=await response.text();if(new TextEncoder().encode(text).length>MAX_BYTES)throw Error('Source exceeds 16 MB');return text;}
  const reader=response.body.getReader(),chunks=[];let size=0;
  try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES)throw Error('Source exceeds 16 MB. Use a paged directory URL.');chunks.push(value);}}
  catch(error){await reader.cancel().catch(()=>{});throw error;}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}return new TextDecoder().decode(bytes);
 }
 function create({seeds=[],sources:initial,onUpdate=()=>{},fetcher=(...args)=>fetch(...args)}={}){
  let configured=sources(initial),revision=0,controller=null,loading=false,reportedTotal=0,records=new Map(),states=[];
  const seedRows=seeds.map(row=>({...row,languages:row.languages||(['telugu','tamil'].includes(row.category)?[row.category]:[]),tags:words(row.tags),country:row.country||(['telugu','tamil','akashvani'].includes(row.category)?'India':'')}));
  function reset(){records=new Map(seedRows.map(row=>[row.streamUrl,row]));states=configured.map(source=>({...source,status:source.enabled?'waiting':'disabled',offset:0,count:0,done:!source.enabled,error:'',fingerprint:''}));reportedTotal=0;}
  function snapshot(){return {stations:[...records.values()],sources:states.map(row=>({...row})),loading,reportedTotal,complete:states.every(row=>row.done)};}
  const emit=()=>onUpdate(snapshot());
  async function request(address,signal){
   const attempt=new AbortController(),cancel=()=>attempt.abort();signal.addEventListener('abort',cancel,{once:true});if(signal.aborted)cancel();
   const timer=setTimeout(cancel,15000);
   try{return await read(await fetcher(address,{signal:attempt.signal,credentials:'omit',referrerPolicy:'no-referrer',cache:'default'}));}
   finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);}
  }
  function pause(){revision++;controller?.abort();loading=false;for(const state of states)if(state.status==='loading')state.status='paused';emit();}
  async function run(){
   if(loading)return;const generation=++revision;controller=new AbortController();const signal=controller.signal;loading=true;emit();
   for(const state of states){
    if(generation!==revision)return;if(state.done||!state.enabled)continue;state.status='loading';state.error='';emit();
    if(state.kind==='stream'){const item=station({name:state.name,url:state.url},state,0);if(item&&!records.has(item.streamUrl)){records.set(item.streamUrl,item);state.count=1;}state.done=true;state.status='ready';emit();continue;}
    if(state.kind==='directory'&&state.url===defaults[0].url&&!reportedTotal){
     void request('https://de1.api.radio-browser.info/json/stats',signal).then(text=>{const stats=JSON.parse(text);if(generation===revision&&Number.isFinite(stats.stations)&&stats.stations<=1000000){reportedTotal=stats.stations;emit();}}).catch(()=>{});
    }
    try{
     let pages=0;
     while(generation===revision&&!signal.aborted){
      const address=new URL(state.url);
      if(state.kind==='directory'){address.searchParams.set('limit',String(PAGE));address.searchParams.set('offset',String(state.offset));address.searchParams.set('order','name');address.searchParams.set('reverse','false');address.searchParams.set('hidebroken','true');}
      const mirrors=state.url===defaults[0].url?['de1.api.radio-browser.info','fr1.api.radio-browser.info','at1.api.radio-browser.info']:[address.hostname];
      let text,lastError;
      for(const host of mirrors){if(signal.aborted)break;try{address.hostname=host;text=await request(address.href,signal);break;}catch(error){lastError=error;}}
      if(text===undefined)throw lastError||Error('Source unavailable');if(generation!==revision)return;
      let rows,rawCount;
      if(state.kind==='m3u'){rows=playlist(text,state);rawCount=rows.length;if(!rows.length&&/^\s*[\[{]/.test(text))throw Error('This looks like JSON. Change the source format to JSON or paged API.');}
      else{const data=JSON.parse(text),list=Array.isArray(data)?data:Array.isArray(data.stations)?data.stations:null;if(!list)throw Error('Expected a JSON array of stations');rawCount=list.length;rows=list.map((row,index)=>station(row,state,index)).filter(Boolean);}
      const fingerprint=`${rawCount}:${rows[0]?.streamUrl}:${rows.at(-1)?.streamUrl}`;
      if(state.kind==='directory'&&state.offset&&rawCount&&fingerprint===state.fingerprint)throw Error('This API ignored pagination. Edit the URL or use JSON list mode.');
      state.fingerprint=fingerprint;state.offset+=rawCount;
      for(const row of rows){if(!records.has(row.streamUrl)){records.set(row.streamUrl,row);state.count++;}}
      state.done=state.kind!=='directory'||rawCount<PAGE;state.status=state.done?'ready':'loading';emit();
      if(state.done)break;if(++pages>=250)throw Error('Large source paused at 250 pages. Resume to continue.');
     }
    }catch(error){if(generation!==revision)return;state.status='error';state.error=error?.message?.slice(0,180)||'Source unavailable. Check browser access and retry.';}
   }
   if(generation===revision){loading=false;emit();}
  }
  function reload(next=configured){pause();configured=sources(next);reset();return run();}
  reset();return Object.freeze({snapshot,load:run,resume:run,pause,reload});
 }
 window.SterlingRadioCatalog=Object.freeze({create,normalizeSources:sources,defaults:()=>sources(undefined),parsePlaylist:playlist,normalizeStation:station,safeUrl:url});
})();

/* Public radio metadata only. Catalogs stay in memory; source settings belong
   to the host's existing owner-scoped Radio configuration. */
(()=>{
 const PAGE=1000,MAX_BYTES=16*1024*1024;
 const WORLD='https://de1.api.radio-browser.info/json/stations',MAX_SOURCES=60,CATEGORIES=['akashvani','shortwave'];
 const defaults=[{id:'radio-browser',name:'Radio Browser · World directory',url:WORLD,kind:'directory',enabled:true,group:'world',category:''}];
 const presets=()=>window.SterlingRadioPresets?.sources?.()||defaults.map(row=>({...row}));
 // fr1, at1, nl1 and fi1 no longer exist (checked 2026-10-22); de1 and de2 do.
 const RB_MIRRORS=['de1.api.radio-browser.info','de2.api.radio-browser.info'];
 const isRadioBrowser=address=>{try{return /(^|\.)api\.radio-browser\.info$/.test(new URL(address).hostname);}catch{return false;}};
 // Radio Browser serves the same query as JSON with tags, language, country and logo; M3U only has names.
 function radioBrowserJson(address){
  if(!isRadioBrowser(address))return null;const u=new URL(address);
  if(u.pathname.startsWith('/m3u/'))u.pathname='/json/'+u.pathname.slice(5);else if(!u.pathname.startsWith('/json/'))return null;
  return {href:u.href,paged:!/\/top(?:click|vote)\/\d+/.test(u.pathname)};
 }
 let regionNames=null;try{regionNames=new Intl.DisplayNames(['en'],{type:'region'});}catch{}
 const countryName=value=>{const text=String(value||'').trim();if(/^[A-Z]{2}$/.test(text)&&regionNames){try{return regionNames.of(text)||text;}catch{}}return text;};
 function url(value,stream=false){
  if(typeof value!=='string'||value.length>4096||/[\x00-\x20<>"']/.test(value))return '';
  try{const u=new URL(value);return (u.protocol==='https:'||(stream&&u.protocol==='http:'))&&!u.username&&!u.password?u.href:'';}catch{return '';}
 }
 function sources(value){
  if(!Array.isArray(value))value=presets();
  const seen=new Set(),ids=new Set();return value.slice(0,MAX_SOURCES).flatMap((row,index)=>{
   if(!row||typeof row!=='object')return [];
   const builtin=row.kind==='builtin'&&typeof row.url==='string'&&/^builtin:[a-z0-9-]{1,64}$/.test(row.url);
   const address=builtin?row.url:url(row.url);
   if(!address||address.length>2048||seen.has(address))return [];seen.add(address);
   let id=typeof row.id==='string'&&/^[a-zA-Z0-9-]{1,64}$/.test(row.id)?row.id:`source-${index}`;let suffix=0;while(ids.has(id))id=`source-${index}-${++suffix}`;ids.add(id);
   const group=typeof row.group==='string'&&row.group.trim()?row.group.trim().slice(0,40):'custom';
   return [{id,name:String(row.name||'Radio source').slice(0,80),url:address,kind:builtin?'builtin':['directory','m3u','json','stream'].includes(row.kind)?row.kind:'m3u',enabled:row.enabled!==false,group,category:CATEGORIES.includes(row.category)?row.category:''}];
  });
 }
 const words=value=>(Array.isArray(value)?value:typeof value==='string'?value.split(/[,;]/):[]).filter(v=>typeof v==='string').map(v=>v.trim().toLowerCase()).filter(Boolean).slice(0,30);
 function station(row,source,index){
  if(!row||typeof row!=='object')return null;
  const stream=url(row.url_resolved||row.streamUrl||row.stream_url||row.url,true),name=String(row.name||row.title||'').trim().slice(0,200);
  if(!stream||!name)return null;
  const languages=words(row.language||row.languages),tags=words(row.tags||row.group);
  const country=countryName(row.country||row.countrycode||(source.category==='akashvani'?'India':'')).slice(0,100),region=typeof row.state==='string'?row.state.replace(/\s+/g,' ').trim().toUpperCase().slice(0,60):'';
  const tuned=window.SterlingRadioBands?.classify({title:name,tags,freqKhz:row.freqKhz},source)||{};
  return {id:`directory-${String(row.stationuuid||row.id||stream).slice(0,4000)}`,kind:'radio',pop:Number(row.clickcount)||Number(row.votes)||0,band:tuned.band||null,freqKhz:tuned.khz||null,category:source.category||(languages.includes('telugu')?'telugu':languages.includes('tamil')?'tamil':'directory'),title:name,groups:source.group?[source.group]:[],region,
   subtitle:[country,languages.slice(0,2).map(word=>word.replace(/\b\w/g,c=>c.toUpperCase())).join(', ')].filter(Boolean).join(' · ')||'Live radio',
   streamUrl:stream,backupUrls:[],artwork:url(row.favicon||row.artwork||row.logo),languages,tags,country,sourceId:source.id,shortFreq:tuned.khz?String(tuned.khz):'LIVE',freqLabel:tuned.khz?`${tuned.khz} kHz`:'Live radio'};
 }
 function playlist(text,source){
  if(/#EXT-X-(?:TARGETDURATION|STREAM-INF|MEDIA-SEQUENCE)/.test(text)){const item=station({name:source.name,url:source.url},source,0);return item?[item]:[];}
  const rows=[];let meta={};
  for(const raw of text.split(/\r?\n/)){
   const line=raw.trim();if(!line)continue;
   if(line.startsWith('#EXTINF:')){
    const attrs={};for(const match of line.matchAll(/([\w-]+)="([^"]*)"/g))attrs[match[1]]=match[2];
    const comma=line.search(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/);meta={name:comma>=0?line.slice(comma+1):'',favicon:attrs['tvg-logo'],group:attrs['group-title'],language:attrs['tvg-language'],country:attrs['tvg-country']};
   }else if(line.startsWith('#EXTIMG:'))meta.favicon=line.slice(8).trim();
   else if(line.startsWith('#EXTGENRE:'))meta.group=line.slice(10).trim();
   else if(!line.startsWith('#')){
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
  const tuneSeed=row=>{const tuned=window.SterlingRadioBands?.classify(row,null)||{};return {...row,band:row.band||tuned.band||null,freqKhz:row.freqKhz||tuned.khz||null};};
  function reset(){records=new Map(seedRows.map(row=>[row.streamUrl,{...tuneSeed(row),groups:[...(row.groups||[])]}]));states=configured.map(source=>({...source,status:source.enabled?'waiting':'disabled',offset:0,count:0,done:!source.enabled,error:'',fingerprint:''}));reportedTotal=0;}
  function snapshot(){return {stations:[...records.values()],sources:states.map(row=>({...row})),loading,reportedTotal,complete:states.every(row=>row.done)};}
  const emit=()=>onUpdate(snapshot());
  async function request(address,signal){
   const attempt=new AbortController(),cancel=()=>attempt.abort();signal.addEventListener('abort',cancel,{once:true});if(signal.aborted)cancel();
   const timer=setTimeout(cancel,15000);
   try{return await read(await fetcher(address,{signal:attempt.signal,credentials:'omit',referrerPolicy:'no-referrer',cache:'default'}));}
   finally{clearTimeout(timer);signal.removeEventListener('abort',cancel);}
  }
  // A station found by several sources keeps one record and gains each source's group and category.
  function add(rows,state){
   for(const row of rows){const existing=records.get(row.streamUrl);state.count++;
    if(!existing){records.set(row.streamUrl,row);continue;}
    for(const group of row.groups)if(!existing.groups?.includes(group))(existing.groups||(existing.groups=[])).push(group);
    if(state.category&&!CATEGORIES.includes(existing.category))existing.category=state.category;
    if(!existing.region&&row.region)existing.region=row.region;
    if(row.band&&(!existing.band||(row.freqKhz&&!existing.freqKhz))){existing.band=row.band;if(row.freqKhz)existing.freqKhz=row.freqKhz;}
   }
  }
  function pause(){revision++;controller?.abort();loading=false;for(const state of states)if(state.status==='loading')state.status='paused';emit();}
  async function run(){
   if(loading)return;const generation=++revision;controller=new AbortController();const signal=controller.signal;loading=true;emit();
   // Small lists first so Akashvani, bands and top stations appear before the 45,000-station directory finishes.
   for(const state of [...states].sort((a,b)=>(a.kind==='directory')-(b.kind==='directory'))){
    if(generation!==revision)return;if(state.done||!state.enabled)continue;state.status='loading';state.error='';emit();
    if(state.kind==='builtin'){const text=window.SterlingRadioPresets?.playlists?.[state.url.slice(8)];if(text)add(playlist(text,state),state);else{state.status='error';state.error='This built-in list is not available in this build.';emit();continue;}state.done=true;state.status='ready';emit();continue;}
    if(state.kind==='stream'){const item=station({name:state.name,url:state.url},state,0);if(item&&!records.has(item.streamUrl)){records.set(item.streamUrl,item);state.count=1;}state.done=true;state.status='ready';emit();continue;}
    const rich=state.kind==='directory'||state.kind==='m3u'?radioBrowserJson(state.url):null,paged=state.kind==='directory'||Boolean(rich?.paged),asJson=state.kind!=='m3u'||Boolean(rich);
    if(state.kind==='directory'&&state.url===WORLD&&!reportedTotal){
     void request('https://de1.api.radio-browser.info/json/stats',signal).then(text=>{const stats=JSON.parse(text);if(generation===revision&&Number.isFinite(stats.stations)&&stats.stations<=1000000){reportedTotal=stats.stations;emit();}}).catch(()=>{});
    }
    try{
     let pages=0;
     while(generation===revision&&!signal.aborted){
      const address=new URL(rich?.href||state.url);
      if(paged){address.searchParams.set('limit',String(PAGE));address.searchParams.set('offset',String(state.offset));if(!address.searchParams.has('order')){address.searchParams.set('order','name');address.searchParams.set('reverse','false');}address.searchParams.set('hidebroken','true');}
      const mirrors=isRadioBrowser(address.href)?RB_MIRRORS:[address.hostname];
      let text,lastError;
      for(const host of mirrors){if(signal.aborted)break;try{address.hostname=host;text=await request(address.href,signal);break;}catch(error){lastError=error;}}
      if(text===undefined)throw lastError||Error('Source unavailable');if(generation!==revision)return;
      let rows,rawCount;
      if(!asJson){rows=playlist(text,state);rawCount=rows.length;if(!rows.length&&/^\s*[\[{]/.test(text))throw Error('This looks like JSON. Change the source format to JSON or paged API.');}
      else{const data=JSON.parse(text),list=Array.isArray(data)?data:Array.isArray(data.stations)?data.stations:null;if(!list)throw Error('Expected a JSON array of stations');rawCount=list.length;rows=list.map((row,index)=>station(row,state,index)).filter(Boolean);}
      const fingerprint=`${rawCount}:${rows[0]?.streamUrl}:${rows.at(-1)?.streamUrl}`;
      if(paged&&state.offset&&rawCount&&fingerprint===state.fingerprint)throw Error('This API ignored pagination. Edit the URL or use JSON list mode.');
      state.fingerprint=fingerprint;state.offset+=rawCount;
      add(rows,state);
      state.done=!paged||rawCount<PAGE;state.status=state.done?'ready':'loading';emit();
      if(state.done)break;if(++pages>=250)throw Error('Large source paused at 250 pages. Resume to continue.');
     }
    }catch(error){if(generation!==revision)return;state.status='error';state.error=error?.message?.slice(0,180)||'Source unavailable. Check browser access and retry.';}
   }
   if(generation===revision){loading=false;emit();}
  }
  function reload(next=configured){pause();configured=sources(next);reset();return run();}
  reset();return Object.freeze({snapshot,load:run,resume:run,pause,reload});
 }
 window.SterlingRadioCatalog=Object.freeze({create,normalizeSources:sources,defaults:()=>sources(undefined),radioBrowserJson,parsePlaylist:playlist,normalizeStation:station,safeUrl:url});
})();

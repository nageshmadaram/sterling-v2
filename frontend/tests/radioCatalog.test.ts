import {readFileSync} from 'node:fs';
import {Script} from 'node:vm';
import {JSDOM} from 'jsdom';
import {afterEach,expect,it} from 'vitest';
const opened:JSDOM[]=[];
afterEach(()=>opened.splice(0).forEach(dom=>dom.window.close()));
function api(){const dom=new JSDOM('',{runScripts:'outside-only'});opened.push(dom);(dom.window as any).TextEncoder=TextEncoder;new Script(readFileSync('media/radio-catalog.js','utf8')).runInContext(dom.getInternalVMContext());return (dom.window as any).SterlingRadioCatalog;}
const source={id:'fixture',name:'Fixture directory',url:'https://directory.fixture/stations',kind:'directory',enabled:true};
const row=(id:number)=>({stationuuid:String(id),name:`Station ${id}`,url_resolved:`https://stream.fixture/${id}.mp3`,language:id%2?'telugu':'english',country:'India',tags:id%2?'news,talk':'rock',favicon:'https://stream.fixture/logo.png'});
const response=(data:unknown)=>({ok:true,headers:{get:()=>null},text:async()=>JSON.stringify(data)});
it('loads every directory page, deduplicates stream URLs and reports actual imported counts',async()=>{
 const serviceApi=api(),offsets:number[]=[];
 const service=serviceApi.create({sources:[source],fetcher:async(address:string)=>{const offset=Number(new URL(address).searchParams.get('offset'));offsets.push(offset);return response(Array.from({length:offset?23:1000},(_,i)=>row(offset+i)));}});
 await service.load();const state=service.snapshot();
 expect(offsets).toEqual([0,1000]);expect(state.stations).toHaveLength(1023);expect(state.complete).toBe(true);expect(state.sources[0].count).toBe(1023);
 expect(state.stations[1]).toMatchObject({languages:['telugu'],tags:['news','talk'],country:'India'});
});
it('source edits, disable and delete remove their stations while retaining built-ins',async()=>{
 const serviceApi=api();const seed={id:'seed',title:'Seed',streamUrl:'https://seed.fixture/live',category:'telugu'};
 const service=serviceApi.create({seeds:[seed],sources:[{...source,kind:'json'}],fetcher:async(address:string)=>response([row(address.includes('updated')?2:1)])});
 await service.load();expect(service.snapshot().stations).toHaveLength(2);
 await service.reload([{...source,kind:'json',url:'https://directory.fixture/updated'}]);expect(service.snapshot().stations[1].title).toBe('Station 2');
 await service.reload([{...source,enabled:false}]);expect(service.snapshot().stations).toHaveLength(1);
 await service.reload([]);expect(service.snapshot().stations[0].id).toBe('seed');expect(service.snapshot().complete).toBe(true);
});
it('pauses after a page and resumes at the next offset without losing loaded rows',async()=>{
 const serviceApi=api(),offsets:number[]=[];let paused=false;let service:any;
 service=serviceApi.create({sources:[source],onUpdate:(state:any)=>{if(!paused&&state.loading&&state.stations.length===1000){paused=true;service.pause();}},fetcher:async(address:string)=>{const offset=Number(new URL(address).searchParams.get('offset'));offsets.push(offset);return response(offset?[]:Array.from({length:1000},(_,i)=>row(i)));}});
 await service.load();expect(service.snapshot().stations).toHaveLength(1000);expect(service.snapshot().loading).toBe(false);
 await service.resume();expect(offsets).toEqual([0,1000]);expect(service.snapshot().complete).toBe(true);
});
it('ignores a late page from a deleted or replaced source',async()=>{
 const serviceApi=api();let resolveOld!:(value:unknown)=>void;
 const service=serviceApi.create({sources:[{...source,kind:'json'}],fetcher:(address:string)=>address.includes('new')?Promise.resolve(response([row(2)])):new Promise(resolve=>{resolveOld=resolve;})});
 const old=service.load();await service.reload([{...source,kind:'json',url:'https://directory.fixture/new'}]);resolveOld(response([row(1)]));await old;
 expect(service.snapshot().stations.map((station:any)=>station.title)).toEqual(['Station 2']);
});
it('parses M3U metadata safely and keeps an HLS manifest as one station',()=>{
 const serviceApi=api();const parsed=serviceApi.parsePlaylist('#EXTM3U\n#EXTINF:-1 group-title="News,Talk" tvg-language="Telugu" tvg-logo="javascript:bad",Test <station>\n/live.mp3\n#EXTINF:-1,Bad\njavascript:alert(1)',source);
 expect(parsed).toHaveLength(1);expect(parsed[0]).toMatchObject({title:'Test <station>',tags:['news','talk'],languages:['telugu'],artwork:'',streamUrl:'https://directory.fixture/live.mp3'});
 const hls=serviceApi.parsePlaylist('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nsegment.ts',source);expect(hls).toHaveLength(1);expect(hls[0].streamUrl).toBe(source.url);
 expect(serviceApi.normalizeSources([{...source,url:'https://user:secret@directory.fixture/stations'},{...source,url:'javascript:bad'}])).toHaveLength(0);
});
it('detects an API that ignores offset instead of endlessly duplicating pages',async()=>{
 const serviceApi=api();let calls=0;const rows=Array.from({length:1000},(_,i)=>row(i));
 const service=serviceApi.create({sources:[source],fetcher:async()=>{calls++;return response(rows);}});await service.load();
 expect(calls).toBe(2);expect(service.snapshot().stations).toHaveLength(1000);expect(service.snapshot().sources[0].error).toContain('ignored pagination');
});
it('supports a manually configured direct stream without downloading audio as a playlist',async()=>{
 const serviceApi=api();let calls=0;const service=serviceApi.create({sources:[{...source,kind:'stream',url:'https://stream.fixture/live.mp3'}],fetcher:async()=>{calls++;throw Error('must not fetch audio');}});await service.load();
 expect(calls).toBe(0);expect(service.snapshot().stations).toHaveLength(1);expect(service.snapshot().stations[0].title).toBe(source.name);
});
it('loads a 50,000-station directory completely while rejecting duplicate source IDs',async()=>{
 const serviceApi=api();const service=serviceApi.create({sources:[source],fetcher:async(address:string)=>{const offset=Number(new URL(address).searchParams.get('offset'));return response(offset>=50000?[]:Array.from({length:1000},(_,i)=>row(offset+i)));}});
 await service.load();expect(service.snapshot().stations).toHaveLength(50000);expect(service.snapshot().complete).toBe(true);
 const list=serviceApi.normalizeSources([source,{...source,url:'https://other.fixture/stations'}]);expect(new Set(list.map((item:any)=>item.id)).size).toBe(2);
},30000);
function withPresets(){const serviceApi=api();const dom=opened.at(-1)!;new Script(readFileSync('media/radio-sources.js','utf8')).runInContext(dom.getInternalVMContext());return serviceApi;}
it('defaults to the grouped source library and keeps built-in playlists editable',()=>{
 const serviceApi=withPresets();const list=serviceApi.defaults();
 expect(list.length).toBeGreaterThan(25);expect(list.find((row:any)=>row.id==='akashvani-pbaudio')).toMatchObject({kind:'builtin',url:'builtin:akashvani-pbaudio',group:'akashvani',category:'akashvani',enabled:true});
 expect(list.find((row:any)=>row.id==='tag-mw').enabled).toBe(false);
 expect(serviceApi.normalizeSources([{id:'x',name:'Bad',url:'builtin:../etc',kind:'builtin'}])).toHaveLength(0);
});
it('reads Radio Browser M3U links as paged JSON on any mirror, and top lists as one request',()=>{
 const serviceApi=api();
 expect(serviceApi.radioBrowserJson('https://de1.api.radio-browser.info/m3u/stations/bylanguageexact/telugu?hidebroken=true')).toEqual({href:'https://de1.api.radio-browser.info/json/stations/bylanguageexact/telugu?hidebroken=true',paged:true});
 expect(serviceApi.radioBrowserJson('https://de1.api.radio-browser.info/m3u/stations/topclick/1000?hidebroken=true').paged).toBe(false);
 expect(serviceApi.radioBrowserJson('https://example.com/m3u/stations/x.m3u')).toBeNull();
});
it('loads a built-in playlist and merges groups when sources overlap',async()=>{
 const serviceApi=withPresets();const stream='https://airhlspush.pc.cdn.bitgravity.com/httppush/hlspbaudio002/hlspbaudio002_Auto.m3u8';
 const service=serviceApi.create({sources:[{id:'akashvani-pbaudio',name:'AIR',url:'builtin:akashvani-pbaudio',kind:'builtin',group:'akashvani',category:'akashvani'},
  {id:'daily',name:'Daily',url:'https://list.fixture/stations.json',kind:'json',group:'india'}],
  fetcher:async()=>response([{name:'Live News 24x7',stream_url:stream,state:' Delhi ',language:'Hindi, English'}])});
 await service.load();const rows=service.snapshot().stations;
 expect(rows).toHaveLength(5);const news=rows.find((row:any)=>row.streamUrl===stream);
 expect(news).toMatchObject({category:'akashvani',country:'India',groups:['akashvani','india'],region:'DELHI',languages:['hindi','english']});
 expect(service.snapshot().sources.map((row:any)=>row.count)).toEqual([5,1]);
});
function withBands(){const serviceApi=api();const dom=opened.at(-1)!;new Script(readFileSync('media/radio-bands.js','utf8')).runInContext(dom.getInternalVMContext());return {serviceApi,bands:(dom.window as any).SterlingRadioBands};}
it('splits AM, shortwave, longwave and ham stations and reads their frequencies',()=>{
 const {bands}=withBands();
 expect(bands.classify({title:'WABC 770 AM',tags:['news']},{id:'tag-am'})).toEqual({band:'mw',khz:770});
 expect(bands.classify({title:'AIR Madurai AM1269',tags:[]},null)).toEqual({band:'mw',khz:1269});
 expect(bands.classify({title:'WRMI Radio Miami International (9955 kHz)',tags:[]},{id:'intl-shortwave'})).toEqual({band:'sw',khz:9955});
 expect(bands.classify({title:'Radio Free Asia 11.7 MHz',tags:['shortwave']},null)).toEqual({band:'sw',khz:11700});
 expect(bands.classify({title:'Longwave 198 kHz',tags:['longwave']},null)).toEqual({band:'lw',khz:198});
 expect(bands.classify({title:'Ham net',tags:['ham radio']},{id:'tag-ham'})).toEqual({band:'ham',khz:null});
 expect(bands.classify({title:'Station 1010 AM',tags:[]},{id:'tag-shortwave'}).band).toBe('mw');
 expect(bands.meterBand('sw',9410)).toBe('31m');
});
it('integrates the five requested feeds as distinct playable home playlists',async()=>{
 const serviceApi=withPresets(),presets=(opened.at(-1)!.window as any).SterlingRadioPresets;
 const feeds=[['lang-telugu','bylanguageexact/telugu','telugu-live'],['rb-ilaiyaraaja','byname/ilaiyaraaja','ilaiyaraaja'],['rb-rahman','byname/rahman','rahman'],['rb-spb','byname/spb','retro'],['rb-bollywood-lofi','byname/bollywood%20lofi','bollywood-lofi']];
 const list=serviceApi.defaults();
 for(const [id,path,group] of feeds){
  const feed=list.find((item:any)=>item.id===id);
  expect(feed).toMatchObject({url:`https://de1.api.radio-browser.info/m3u/stations/${path}?hidebroken=true`,enabled:true,kind:'m3u',group});
  expect(presets.homePlaylists).toContain(group);
 }
 const requested=feeds.map(([id])=>list.find((item:any)=>item.id===id));
 // Overlapping results stay playable once, with membership in every requested playlist.
 const service=serviceApi.create({sources:requested,fetcher:async(address:string)=>{
  expect(address).toContain('/json/stations/');expect(new URL(address).searchParams.get('hidebroken')).toBe('true');
  return response([row(1)]);
 }});
 await service.load();const state=service.snapshot();
 expect(state.complete).toBe(true);expect(state.stations).toHaveLength(1);
 expect(state.stations[0].groups).toEqual(feeds.map(feed=>feed[2]));
 expect(state.stations[0].streamUrl).toBe('https://stream.fixture/1.mp3');
});
it('imports all supplied direct stations in five Music Mixes groups and keeps YouTube out of audio',async()=>{
 const serviceApi=withPresets(),presets=(opened.at(-1)!.window as any).SterlingRadioPresets;
 const counts={'telugu-247':4,'telugu-nri':6,'telangana-air':7,'andhra-air':10,'telugu-devotional':4};
 const sources=serviceApi.defaults().filter((s:any)=>s.kind==='builtin'&&s.group in counts);
 expect(sources).toHaveLength(5);
 const service=serviceApi.create({sources,fetcher:()=>{throw Error('built-in lists must not fetch');}});
 await service.load();const state=service.snapshot();
 expect(state.stations).toHaveLength(31);expect(state.complete).toBe(true);
 for(const [group,count] of Object.entries(counts)){
  expect(state.stations.filter((s:any)=>s.groups.includes(group))).toHaveLength(count);
  expect(presets.musicMixGroups).toContain(group);
 }
 expect(state.stations.some((s:any)=>s.streamUrl.includes('youtube.com'))).toBe(false);
 expect(presets.youtubeMixes).toHaveLength(7);
 expect(presets.youtubeMixes.filter((s:any)=>s.url.includes('/playlist?'))).toHaveLength(1);
 for(const artist of ['chithra','janaki','ghantasala']){
  expect(serviceApi.defaults().find((s:any)=>s.id===`rb-${artist}`)).toMatchObject({enabled:true,group:artist,url:`https://de1.api.radio-browser.info/m3u/stations/byname/${artist}?hidebroken=true`});
 }
 // The expanded library must leave room for existing personal sources during migration.
 const personal={...source,id:'personal',kind:'m3u'};
 expect(serviceApi.normalizeSources([...serviceApi.defaults(),personal]).at(-1).id).toBe('personal');
});

import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {Script} from 'node:vm';
import {JSDOM,VirtualConsole} from 'jsdom';
import {afterEach,describe,expect,it} from 'vitest';

const opened:JSDOM[]=[];
afterEach(()=>{opened.splice(0).forEach(dom=>dom.window.close());});
function boot(kind:'tv'|'radio') {
  const html=readFileSync(resolve(process.cwd(),`public/${kind}/index.html`),'utf8');
  const errors:string[]=[];
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(html,{url:`https://sterling.example/${kind}/index.html`,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});opened.push(dom);
  const w=dom.window;const pending:FrameRequestCallback[]=[];
  Object.defineProperty(w,'parent',{value:{postMessage(){}}});
  Object.defineProperty(w,'innerWidth',{value:390});Object.defineProperty(w,'innerHeight',{value:844});
  w.matchMedia=(()=>({matches:true,addEventListener(){},removeEventListener(){}})) as any;
  w.requestAnimationFrame=callback=>{pending.push(callback);return pending.length;};
  w.HTMLElement.prototype.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.HTMLMediaElement.prototype.load=()=>{};w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.play=()=>Promise.resolve();
  w.HTMLCanvasElement.prototype.getContext=(()=>new Proxy({createLinearGradient:()=>({addColorStop(){}}),measureText:()=>({width:30})},{get:(target,key)=>key in target?(target as any)[key]:()=>{}})) as any;
  w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,iVBORw0KGgo=';
  w.fetch=(async()=>({ok:true,text:async()=> '#EXTM3U',json:async()=>[]})) as any;
  w.addEventListener('error',event=>errors.push(event.message));
  for(const file of kind==='radio'?['radio-bands.js','radio-catalog.js','radio-directory.js','sheet-swipe.js']:['sheet-swipe.js'])new Script(readFileSync(resolve(process.cwd(),'media',file),'utf8')).runInContext(dom.getInternalVMContext());
  for(const script of [...w.document.querySelectorAll('script:not([src])')]) {
    // Direct-navigation redirect is deliberately excluded from an embedded-frame boot.
    if(script.textContent?.includes('window.top === window.self'))continue;
    try{new Script(script.textContent||'').runInContext(dom.getInternalVMContext());}catch(error){errors.push(String(error));}
  }
  for(const element of [...w.document.querySelectorAll('*')]){for(const attribute of [...element.attributes]){if(/^on[a-z]+$/.test(attribute.name))(element as any)[attribute.name]=new w.Function('event',attribute.value);}}
  Object.defineProperty(w.document,'readyState',{value:'complete',configurable:true});
  w.dispatchEvent(new w.Event('DOMContentLoaded'));
  w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:`sterling.${kind}.hydrate`,hostHeader:true,values:kind==='radio'?{omnicast_v11_config:JSON.stringify({catalogSources:[],bootBehavior:'quiet'})}:{}}}));
  const flush=()=>{pending.splice(0).forEach(callback=>callback(0));};flush();
  return {w,errors,flush};
}

describe('Actual sandboxed media documents on mobile',()=>{
  it('boots TV with a matching engine and makes theme, channel, and search controls actionable',()=>{
    const {w,errors,flush}=boot('tv');
    expect(errors).toEqual([]);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.tv.command',action:'theme',value:'spotify'}}));flush();
    expect(w.document.body.classList.contains('theme-spotify')).toBe(true);
    const card=w.document.querySelector('.tm-quick [data-tm-channel]') as HTMLButtonElement;expect(card).not.toBeNull();
    const touch=(type:string,y:number)=>{const event=new w.Event(type,{bubbles:true,cancelable:true});Object.defineProperty(event,'touches',{value:[{clientX:30,clientY:y}]});Object.defineProperty(event,'changedTouches',{value:[{clientX:30,clientY:y}]});card.dispatchEvent(event);return event;};
    touch('touchstart',100);expect(touch('touchmove',106).defaultPrevented).toBe(false);touch('touchend',106);
    card.click();flush();
    expect(w.document.body.classList.contains('tm-now-open')).toBe(true);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.tv.command',action:'search',value:'Red Bull'}}));flush();
    expect(w.document.body.classList.contains('tm-catalog-view')).toBe(true);
    expect(w.document.querySelector('#yt-studio-grid')?.textContent).toContain('Red Bull');
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.tv.command',action:'theme',value:'youtube'}}));flush();
    (w.document.querySelector('#yt-studio-grid .channel-grid-card h4') as HTMLElement).click();flush();
    expect(w.document.getElementById('active-title')?.textContent).toBe('Red Bull TV Global Sports');
    expect((w.document.getElementById('search-input') as HTMLInputElement)?.value).toBe('');
    expect((w.document.getElementById('tm-search-input') as HTMLInputElement)?.value).toBe('');
    expect(w.document.body.classList.contains('tm-watch-view')).toBe(true);
    expect(errors).toEqual([]);
  });
  it('separates Radio home discovery, categories and the dedicated SW player',()=>{
    const {w,errors}=boot('radio');
    expect(w.document.documentElement.dataset.radioCategory).toBe('all');
    expect(w.document.getElementById('sp-collections')?.textContent).toContain('Shortwave & AM');
    (w.document.getElementById('sp-mchip-te') as HTMLElement).click();
    expect(w.document.documentElement.dataset.radioCategory).toBe('telugu');
    expect(w.document.getElementById('sp-grid-heading')?.textContent).toContain('Telugu');
    expect(w.eval('catalogItems.every(ch=>ch.category=== "telugu")')).toBe(true);
    (w.document.getElementById('sp-mchip-all') as HTMLElement).click();
    expect(w.document.documentElement.dataset.radioCategory).toBe('all');
    (w as any).openMobileTuner();
    const panel=w.document.getElementById('sw-player-window')!;
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.querySelectorAll('.sw-player-presets button').length).toBeGreaterThan(0);
    (panel.querySelector('.sw-player-presets button') as HTMLButtonElement).click();
    expect(panel.querySelector('.sw-player-title')?.textContent).not.toBe('');
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.command',action:'minimize'}}));
    expect(panel.classList.contains('hidden')).toBe(true);
    expect(errors).toEqual([]);
  });
  it.each(['radio','tv'] as const)('publishes and selects real %s search suggestions',kind=>{
    const {w,errors,flush}=boot(kind);
    const received:any[]=[];w.parent.postMessage=(message:any)=>received.push(message);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:`sterling.${kind}.command`,action:'suggest',requestId:'audit-request-1',value:kind==='tv'?'Red Bull':'BBC'}}));
    const payload=received.find(message=>message.type===`sterling.${kind}.suggestions`);
    expect(payload.requestId).toBe('audit-request-1');
    expect(payload.items.length).toBeGreaterThan(0);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:`sterling.${kind}.command`,action:'select-suggestion',value:payload.items[0].id}}));flush();
    expect(w.document.getElementById(kind==='tv'?'active-title':'sheet-title')?.textContent).toContain(kind==='tv'?'Red Bull':'BBC');
    expect(errors).toEqual([]);
  });
  it('never mutes on its own when the browser blocks autoplay; it asks for a tap',async()=>{
    const {w}=boot('tv');
    await new Promise(resolve=>setTimeout(resolve,0));
    const video=w.document.getElementById('video-player') as HTMLVideoElement;
    w.eval('preferredTvMuted=false');
    w.HTMLMediaElement.prototype.play=()=>Promise.reject(new w.DOMException('Gesture required','NotAllowedError'));
    w.eval('safeAutoplayVideo(document.getElementById("video-player"))');
    await new Promise(resolve=>setTimeout(resolve,0));
    expect(video.muted).toBe(false);
    expect(w.eval('preferredTvMuted')).toBe(false);
    // No 'Tap to play' overlay; the next gesture anywhere resumes with sound.
    expect(w.document.getElementById('tv-mobile-play')?.classList.contains('hidden')).toBe(true);
    w.HTMLMediaElement.prototype.play=()=>Promise.resolve();
    w.eval('resumeTvPlayback()');await Promise.resolve();
    expect(video.muted).toBe(false);
    w.eval('toggleMuteAudio()');
    expect(w.eval('preferredTvMuted')).toBe(true);
  });
  it('finds TV channels with punctuation-insensitive multiword queries',()=>{
    const {w}=boot('tv');
    expect(w.eval('getRankedSearchMatches("red-bull").length')).toBeGreaterThan(0);
  });
  it('boots Radio and responds to category and player-sheet taps',async()=>{
    const {w,errors}=boot('radio');
    (w.document.querySelector('#sp-mchip-te') as HTMLButtonElement).click();
    expect(w.document.querySelector('#sp-grid-heading')?.textContent).toContain('Telugu');
    // Station cards are inserted after boot; they must work without compiling
    // inline attributes (as on a proxy with script-src-attr 'none').
    const station=w.document.querySelector('#sp-grid [data-radio-click]') || w.document.querySelector('[data-radio-arg]');
    expect(station).not.toBeNull();
    (station as HTMLElement).click();
    expect(w.document.getElementById('sheet-title')?.textContent).not.toBe('Select a Station');
    const mini=w.document.querySelector('#spotify-app .solid-sp-miniplayer') as HTMLElement;mini.click();
    const sheet=w.document.getElementById('mobile-player-sheet')!;
    expect(sheet.classList.contains('drawer-visible')).toBe(true);
    const touch=(target:Element,type:string,y:number)=>{const event=new w.Event(type,{bubbles:true});Object.defineProperty(event,'touches',{value:[{clientX:20,clientY:y}]});Object.defineProperty(event,'changedTouches',{value:[{clientX:20,clientY:y}]});target.dispatchEvent(event);};
    const volume=w.document.getElementById('sheet-range')!;touch(volume,'touchstart',0);touch(volume,'touchend',180);
    expect(sheet.classList.contains('drawer-visible')).toBe(true);
    // Swipe down from the header: the sheet follows the finger, then closes (media/sheet-swipe.js).
    const handle=sheet.firstElementChild!.querySelector('span')!;touch(handle,'touchstart',0);
    for(const y of [20,60,120,180])touch(handle,'touchmove',y);touch(handle,'touchend',180);
    await new Promise(resolve=>setTimeout(resolve,250));
    expect(sheet.classList.contains('drawer-hidden')).toBe(true);
    expect(errors).toEqual([]);
  });
  it('renders local Radio search immediately and ignores stale network results',async()=>{
    const {w,errors}=boot('radio');
    let release:(value:unknown)=>void=()=>{};
    w.fetch=((url:string)=>url.includes('/byname/bbc')?new Promise(resolve=>{release=resolve;}):Promise.resolve({ok:true,json:async()=>[]})) as any;
    const old=(w as any).runOmniSearch('BBC');
    expect(w.document.querySelector('#sp-cards-grid')?.textContent).toContain('BBC');
    await (w as any).runOmniSearch('Telugu');
    release({ok:true,json:async()=>[{name:'Stale BBC result',url_resolved:'https://old.example/live.mp3',stationuuid:'old'}]});
    await old;
    expect(w.document.querySelector('#sp-grid-heading')?.textContent).toContain('Telugu');
    expect(w.document.querySelector('#sp-cards-grid')?.textContent).not.toContain('Stale BBC result');
    (w as any).onSearchInput('');
    expect(w.document.documentElement.classList.contains('radio-searching')).toBe(false);
    expect(w.document.querySelector('#sp-grid-heading')?.textContent).toContain('Explore all');
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.command',action:'theme',value:'youtube'}}));
    expect(w.document.body.classList.contains('mode-ytmusic')).toBe(true);
    expect(errors).toEqual([]);
  });

  it('closes TV full-player on host Back without ending playback',()=>{
    const {w,errors,flush}=boot('tv');
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.tv.command',action:'theme',value:'spotify'}}));flush();
    const channel=w.document.querySelector('.tm-quick [data-tm-channel]') as HTMLButtonElement;
    channel.click();flush();
    expect(w.document.body.classList.contains('tm-now-open')).toBe(true);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.tv.command',action:'collapse'}}));flush();
    expect(w.document.body.classList.contains('tm-now-open')).toBe(false);
    expect(w.document.getElementById('video-player')).toBeTruthy();
    expect(w.document.body.classList.contains('tm-catalog-view')).toBe(false);
    expect(errors).toEqual([]);
  });

  it('keeps Radio mobile settings interactive and minimizes on host Back',()=>{
    const {w,errors}=boot('radio');
    (w.document.querySelector('#spotify-app .solid-sp-miniplayer') as HTMLElement).click();
    const sheet=w.document.getElementById('mobile-player-sheet')!;
    expect(sheet.classList.contains('drawer-visible')).toBe(true);
    (sheet.querySelector('[data-radio-click="29"]') as HTMLElement).click();
    expect(w.document.getElementById('audio-dsp-modal')?.classList.contains('hidden')).toBe(false);
    expect(w.document.documentElement.classList.contains('radio-modal-open')).toBe(true);
    (w.document.querySelector('#dsp-flat') as HTMLElement).click();
    (w.document.querySelector('#audio-dsp-modal [data-radio-click="9"]') as HTMLElement).click();
    expect(w.document.documentElement.classList.contains('radio-modal-open')).toBe(false);
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.command',action:'minimize'}}));
    expect(sheet.classList.contains('drawer-hidden')).toBe(true);
    // Tuning belongs in Home, not the full-screen now-playing sheet.
    // A tap on the Home tuner must open the drawer without reopening playback.
    expect(sheet.querySelector('[data-radio-click="74"]')).toBeNull();
    const homeTuner=w.document.querySelector('#sw-tuner-deck [data-radio-click="74"]') as HTMLElement | null;
    expect(homeTuner).not.toBeNull();
    homeTuner!.click();
    expect(w.document.getElementById('sw-player-window')?.classList.contains('hidden')).toBe(false);
    expect(w.document.getElementById('sw-player-window')?.getAttribute('aria-label')).toBe('Shortwave and AM player');
    expect(sheet.classList.contains('drawer-hidden')).toBe(true);
    expect(errors).toEqual([]);
  });

});

describe('Media audit regressions',()=>{
 it('Radio next and previous follow the active direct selection instead of an old index',()=>{
  const {w,errors}=boot('radio');
  w.eval(`catalogItems=[0,1,2].map(i=>({id:'audit'+i,title:'Audit '+i,subtitle:'Radio',kind:'radio',streamUrl:'https://fixture.test/'+i+'.mp3',backupUrls:[]}));activeIndex=0;startPlayback(catalogItems[1]);playNextChannel();`);
  expect(w.eval('activeStream.id')).toBe('audit2');w.eval('playPrevChannel()');expect(w.eval('activeStream.id')).toBe('audit1');
  expect(errors).toEqual([]);
 });
 it('Radio does not restart from a late manifest after the sleep timer pauses it',()=>{
  const {w}=boot('radio');let attempts=0;
  w.HTMLMediaElement.prototype.play=()=>{attempts++;return Promise.resolve();};
  w.eval(`window.Hls=class {static Events={MANIFEST_PARSED:'manifest',ERROR:'error'};static isSupported(){return true;}constructor(){window.auditHls=this;this.handlers={};}on(name,callback){this.handlers[name]=callback;}loadSource(){}attachMedia(){}destroy(){}};startPlayback({id:'audit',title:'Audit',kind:'radio',streamUrl:'https://fixture.test/live.m3u8',backupUrls:[]});`);
  w.dispatchEvent(new w.Event('sterling-media-sleep'));w.eval(`auditHls.handlers.manifest()`);
  expect(attempts).toBe(0);expect(w.eval('userPaused')).toBe(true);
 });
 it('TV does not retry muted autoplay after a paused pending play promise fails',async()=>{
  const {w}=boot('tv');let attempts=0,rejectPlay:(reason:Error)=>void=()=>{};
  w.HTMLMediaElement.prototype.play=()=>{attempts++;return new Promise<void>((_resolve,reject)=>{rejectPlay=reject;});};
  w.eval(`safeAutoplayVideo(document.getElementById('video-player'))`);
  w.dispatchEvent(new w.Event('sterling-media-sleep'));rejectPlay(new Error('aborted'));
  await Promise.resolve();await Promise.resolve();
  expect(attempts).toBe(1);expect(w.eval('tvUserPaused')).toBe(true);
 });
 it('Radio tolerates malformed station lists and counters from saved preferences',()=>{
  const {w,errors}=boot('radio');
  w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.hydrate',values:{omnicast_v11_recents:'[null,5,{}]',omnicast_v11_liked:'[null,"bad"]',omnicast_v11_counts:'{"bad":null}',omnicast_v11_config:'{"volume":"bad","muted":[],"activeCategory":null}'}}}));
  expect(w.eval('recentsList.length+likedStations.length')).toBe(0);expect(w.eval('appConfig.volume')).toBe(0.85);expect(errors).toEqual([]);
 });
});

it('Radio discards stale podcast responses and exposes a retry on feed failure',async()=>{
 const {w,errors}=boot('radio');const pending:Array<(data:any)=>void>=[];
 w.fetch=(()=>new Promise(resolve=>pending.push(data=>resolve({ok:true,json:async()=>data})))) as any;
 const first=(w as any).openPodcastShow({id:'a',title:'First',feedUrl:'https://fixture.test/a.xml'});
 const second=(w as any).openPodcastShow({id:'b',title:'Second',feedUrl:'https://fixture.test/b.xml'});
 pending[1]({status:'ok',items:[{title:'Second episode',enclosure:{link:'https://fixture.test/b.mp3'}}]});await second;
 pending[0]({status:'ok',items:[{title:'Stale episode',enclosure:{link:'https://fixture.test/a.mp3'}}]});await first;
 expect(w.eval('episodeList[0].title')).toBe('Second episode');
 w.eval(`episodeList.push({id:'next',title:'Next episode',kind:'podcast_episode',streamUrl:'https://fixture.test/next.mp3'});playNextChannel();`);
 expect(w.eval('activeStream.id')).toBe('next');
 w.fetch=(async()=>{throw Error('offline');}) as any;
 await (w as any).openPodcastShow({id:'c',title:'Failed',feedUrl:'https://fixture.test/c.xml'});
 expect(w.document.getElementById('sp-pod-episodes')?.textContent).toContain('Retry episodes');expect(errors).toEqual([]);
});
it('TV reports clipboard failure instead of claiming a successful share',async()=>{
 const {w}=boot('tv');Object.defineProperty(w.navigator,'clipboard',{value:{writeText:()=>Promise.reject(Error('denied'))},configurable:true});
 await (w as any).copyStreamShareLink();expect(w.document.getElementById('toast')?.textContent).toContain('Clipboard unavailable');
});

it('TV sleep also stops an embedded fallback and explicit resume restores it',()=>{
 const {w,flush}=boot('tv');
 w.eval(`activeChannel={name:'Embedded audit',url:'https://fixture.test/live.m3u8',ytEmbed:'https://www.youtube.com/embed/audit'};document.getElementById('video-player').classList.add('hidden');const frame=document.getElementById('embed-fallback');frame.classList.remove('hidden');frame.src=activeChannel.ytEmbed;`);
 w.dispatchEvent(new w.Event('sterling-media-sleep'));flush();
 expect(w.document.getElementById('embed-fallback')?.getAttribute('src')).toBeNull();
 expect(w.document.querySelector('#tv-now-playing [data-tm-action=play]')?.getAttribute('aria-label')).toBe('Play live TV');
 w.eval('resumeTvPlayback()');flush();expect(w.document.getElementById('embed-fallback')?.getAttribute('src')).toContain('/embed/audit');
 expect(w.document.querySelector('#tv-now-playing [data-tm-action=play]')?.getAttribute('aria-label')).toBe('Pause live TV');
});

it('Radio keeps stable prefix ranking, accent matching and eight unique suggestions',()=>{
 const {w}=boot('radio');
 const rows:Array<{id:string;title:string;subtitle:string;category:string;freqKhz?:number}>=[{id:'late',title:'Other station',subtitle:'news café',category:'global'},
  ...Array.from({length:1200},(_,i)=>({id:`station-${i}`,title:i%3?`Network ${i}`:`Café News ${i}`,subtitle:'news café',category:'global',freqKhz:738})),
  {id:'late',title:'Café News duplicate',subtitle:'news café',category:'global'}];
 w.eval(`MASTER_POOL.splice(0,MASTER_POOL.length,...${JSON.stringify(rows)});catalogItems=[];`);
 const norm=(value:unknown)=>String(value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
 for(const query of ['cafe','news cafe','738','Network','missing']){
  const terms=norm(query).trim().split(/\s+/);
  const reference=rows.filter((item,i,all)=>all.findIndex(other=>other.id===item.id)===i&&terms.every(term=>norm([item.title,item.subtitle,item.category,item.freqKhz].join(' ')).includes(term)))
   .sort((a,b)=>Number(norm(b.title).startsWith(norm(query)))-Number(norm(a.title).startsWith(norm(query)))).slice(0,8);
  expect(Array.from(w.eval(`radioSuggestionItems(${JSON.stringify(query)})`),(item:any)=>item.id)).toEqual(reference.map(item=>item.id));
 }
});

it('Radio reuses normalized catalog text and refreshes mutated station metadata',()=>{
 const {w}=boot('radio');
 const rows=Array.from({length:2000},(_,i)=>({id:`station-${i}`,title:`Station ${i}`,subtitle:'News',category:'global',freqKhz:738}));
 w.eval(`MASTER_POOL.splice(0,MASTER_POOL.length,...${JSON.stringify(rows)});catalogItems=[];window.auditNormalizations=0;window.auditNormalize=String.prototype.normalize;String.prototype.normalize=function(...args){window.auditNormalizations++;return window.auditNormalize.apply(this,args);};`);
 expect(w.eval('getRadioSearchMatches(MASTER_POOL,"news").length')).toBe(2000);
 const cold=w.eval('auditNormalizations');expect(cold).toBeGreaterThan(2000);
 w.eval('auditNormalizations=0');expect(w.eval('getRadioSearchMatches(MASTER_POOL,"news").length')).toBe(2000);
 expect(w.eval('auditNormalizations')).toBe(1);
 w.eval('MASTER_POOL[0].title="Updated Telugu";MASTER_POOL[0].subtitle="Sports";MASTER_POOL[0].category="telugu";MASTER_POOL[0].freqKhz=820;');
 expect(w.eval('getRadioSearchMatches(MASTER_POOL,"updated sports telugu 820")[0].id')).toBe('station-0');
 expect(w.eval('getRadioSearchMatches(MASTER_POOL,"news").length')).toBe(1999);
});

it('TV bounded suggestions match full stable ranking, including duplicate aliases and metadata changes',()=>{
 const {w}=boot('tv');
 const rows=Array.from({length:3000},(_,i)=>({name:i%5?`Network ${i}`:`News ${i}`,url:`https://fixture.test/${i%1500}.m3u8`,group:i%2?'News':'Sports',lang:i%3?'Telugu':'English',verified:i%7===0}));
 rows.push({name:'News',url:'https://fixture.test/0.m3u8',group:'News',lang:'Telugu',verified:true});
 w.eval(`allChannels=${JSON.stringify(rows)};`);
 for(const query of ['news','sports','telugu','news telugu','network','English','missing']){
  const full=Array.from(w.eval(`getRankedSearchMatches(${JSON.stringify(query)})`)) as any[];
  const seen=new Set();const expected=full.filter(row=>{if(seen.has(row.url))return false;seen.add(row.url);return true;}).slice(0,8);
  expect(Array.from(w.eval(`getRankedSearchMatches(${JSON.stringify(query)},8)`),(row:any)=>({name:row.name,url:row.url}))).toEqual(expected.map(row=>({name:row.name,url:row.url})));
 }
 expect(w.eval('getRankedSearchMatches("news").length')).toBeGreaterThan(1000);
 w.eval('allChannels=[allChannels[0]];getRankedSearchMatches("news");allChannels[0].name="Updated channel";allChannels[0].group="Documentary";allChannels[0].lang="Tamil";');
 expect(w.eval('getRankedSearchMatches("updated documentary tamil",8).length')).toBe(1);
 expect(w.eval('getRankedSearchMatches("news",8).length')).toBe(0);
});

it('Radio refreshes remote suggestions using the latest request ID for the same query',async()=>{
 const {w}=boot('radio');const received:any[]=[];w.parent.postMessage=(message:any)=>received.push(message);
 let resolveRemote!:(value:unknown)=>void;
 w.fetch=(()=>new Promise(resolve=>{resolveRemote=resolve;})) as any;
 const search=w.eval('runOmniSearch("Unique remote fixture")');
 const request=(requestId:string)=>w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.command',action:'suggest',value:'Unique remote fixture',requestId}}));
 request('remote-old');request('remote-current');
 resolveRemote({ok:true,json:async()=>[{stationuuid:'remote-fixture',name:'Unique remote fixture',url_resolved:'https://fixture.test/remote.mp3',country:'Test',bitrate:128}]});
 await search;
 const updates=received.filter(message=>message.type==='sterling.radio.suggestions');
 expect(updates.at(-1).requestId).toBe('remote-current');
 expect(updates.at(-1).items[0]).toMatchObject({id:'remote-fixture',label:'Unique remote fixture'});
});


it.each(['radio','tv'] as const)('opens the %s URL source manager through its host command',kind=>{
 const {w,errors}=boot(kind);
 w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:`sterling.${kind}.command`,action:'sources'}}));
 if(kind==='radio')expect((w.document.querySelector('.radio-source-dialog') as HTMLDialogElement).open).toBe(true);
 else expect(w.document.getElementById('source-manager-modal')?.classList.contains('hidden')).toBe(false);
 expect(errors).toEqual([]);
});

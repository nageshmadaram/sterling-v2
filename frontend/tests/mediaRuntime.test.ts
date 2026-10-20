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
  w.HTMLMediaElement.prototype.load=()=>{};w.HTMLMediaElement.prototype.pause=()=>{};w.HTMLMediaElement.prototype.play=()=>Promise.resolve();
  w.HTMLCanvasElement.prototype.getContext=(()=>new Proxy({createLinearGradient:()=>({addColorStop(){}}),measureText:()=>({width:30})},{get:(target,key)=>key in target?(target as any)[key]:()=>{}})) as any;
  w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,iVBORw0KGgo=';
  w.fetch=(async()=>({ok:true,text:async()=> '#EXTM3U',json:async()=>[]})) as any;
  w.addEventListener('error',event=>errors.push(event.message));
  for(const script of [...w.document.querySelectorAll('script:not([src])')]) {
    // Direct-navigation redirect is deliberately excluded from an embedded-frame boot.
    if(script.textContent?.includes('window.top === window.self'))continue;
    try{new Script(script.textContent||'').runInContext(dom.getInternalVMContext());}catch(error){errors.push(String(error));}
  }
  for(const element of [...w.document.querySelectorAll('*')]){for(const attribute of [...element.attributes]){if(/^on[a-z]+$/.test(attribute.name))(element as any)[attribute.name]=new w.Function('event',attribute.value);}}
  Object.defineProperty(w.document,'readyState',{value:'complete',configurable:true});
  w.dispatchEvent(new w.Event('DOMContentLoaded'));
  w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:`sterling.${kind}.hydrate`,hostHeader:true,values:{}}}));
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
    expect(errors).toEqual([]);
  });
  it('boots Radio and responds to category and player-sheet taps',()=>{
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
    const handle=sheet.firstElementChild!.querySelector('span')!;touch(handle,'touchstart',0);touch(handle,'touchend',180);
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
    expect(w.document.querySelector('#sp-grid-heading')?.textContent).toContain('Shortwave');
    w.dispatchEvent(new w.MessageEvent('message',{source:w.parent,data:{type:'sterling.radio.command',action:'theme',value:'youtube'}}));
    expect(w.document.body.classList.contains('mode-ytmusic')).toBe(true);
    expect(errors).toEqual([]);
  });

});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import mobileScript from '../../../../tv-mobile/mobile.js?raw';

const channel={name:'Channel One',url:'https://example.com/one.m3u8',group:'News',lang:'English'};
const other={name:'Channel Two',url:'https://example.com/two.m3u8',group:'Sports',lang:'English'};
let env: Record<string, any>;
let originalPlay: ReturnType<typeof vi.fn>, originalRender: ReturnType<typeof vi.fn>;
let documentEvents: ReturnType<typeof vi.spyOn>, windowEvents: ReturnType<typeof vi.spyOn>;
function click(selector:string){(document.querySelector(selector) as HTMLButtonElement).click();}
beforeEach(()=>{
  document.body.className='theme-youtube';
  document.body.innerHTML=`<div id="app-workspace"><main id="main-scroll"><div id="catalog-section"><div id="catalog-lang-select"><option value="All">All</option></div><div id="catalog-genre-select"></div></div><video id="video-player"></video></main></div><input id="search-input"/>`;
  const media=new EventTarget() as EventTarget & {matches:boolean};media.matches=true;
  vi.stubGlobal('matchMedia',()=>media);
  vi.stubGlobal('requestAnimationFrame',(fn:()=>void)=>{fn();return 1;});
  vi.stubGlobal('innerWidth',390);vi.stubGlobal('innerHeight',844);
  (document.getElementById('main-scroll') as any).scrollTo=vi.fn();
  localStorage.clear();
  env={rebuildCombinedChannelCatalog:vi.fn(),currentTheme:'youtube',activeChannel:channel,allChannels:[channel,other],VERIFIED_STREAMS:[channel,other],recentHistory:[channel],favorites:[],INDIAN_LANG_IDS:['Indian'],catalogScope:'all',catalogCategory:'All',catalogLanguage:'All',hls:null,searchInputEl:document.getElementById('search-input'),
    escapeAttr:(v:string)=>v.replaceAll('&','&amp;').replaceAll('"','&quot;'),escapeHtml:(v:string)=>v.replaceAll('&','&amp;').replaceAll('<','&lt;'),
    switchTheme:vi.fn((theme:string)=>{env.currentTheme=theme;document.body.className='theme-'+theme;}),playStream:vi.fn((ch:typeof channel)=>{env.activeChannel=ch;}),renderViewport:vi.fn(),renderNextBatch:vi.fn(),filteredChannels:[channel,other],renderCatalogControls:vi.fn(),dockVideoToTopLeftMini:vi.fn(),closeSearchDropdown:vi.fn(),computeRightRailPool:()=>[other],hasNativeHls:()=>false,
    toggleSubscribeAndFavorite:vi.fn(()=>{env.favorites=env.favorites.length?[]:[env.activeChannel];}),EQ_PRESETS:{flat:{label:'Flat'},voice:{label:'Vocal'}},applyAudioPreset:vi.fn(),setHlsQualityLevel:vi.fn(),handleCatalogLanguageChange:vi.fn(),handleCatalogGenreChange:vi.fn()};
  originalPlay=env.playStream;originalRender=env.renderViewport;
  documentEvents=vi.spyOn(document,'addEventListener');windowEvents=vi.spyOn(window,'addEventListener');
  new Function('env',`with(env) { ${mobileScript} }`)(env);
});
afterEach(()=>{for(const [name,listener,options] of documentEvents.mock.calls as any)document.removeEventListener(name,listener,options);for(const [name,listener,options] of windowEvents.mock.calls as any)window.removeEventListener(name,listener,options);vi.restoreAllMocks();document.body.innerHTML='';vi.unstubAllGlobals();});

describe('Mobile TV theme interactions',()=>{
  it('switches to Spotify without replacing or cloning the video',()=>{
    const video=document.querySelector('video');click('[data-tm-action="spotify"]');
    expect(document.body.classList.contains('theme-spotify')).toBe(true);
    expect(document.querySelectorAll('video')).toHaveLength(1);expect(document.querySelector('video')).toBe(video);
    expect(document.querySelector('#tv-mobile-home')?.textContent).toContain('Jump back in');
  });
  it('plays the exact selected channel and expands the existing player',()=>{
    click('[data-tm-action="spotify"]');click(`[data-tm-channel="${other.url}"]`);
    expect(originalPlay).toHaveBeenCalledWith(other,0);expect(document.body.classList.contains('tm-now-open')).toBe(true);
    expect(document.getElementById('tm-now-title')?.textContent).toBe(other.name);
    click('[data-tm-action="collapse"]');expect(document.body.classList.contains('tm-now-open')).toBe(false);
  });
  it('uses shared saved channels and search',()=>{
    click('[data-tm-action="spotify"]');click('#tv-mobile-mini [data-tm-action="save"]');
    expect(env.toggleSubscribeAndFavorite).toHaveBeenCalledOnce();click('#tv-mobile-nav [data-tm-action="library"]');
    expect(env.catalogScope).toBe('favs');expect(document.body.classList.contains('tm-catalog-view')).toBe(true);
    click('#tv-mobile-nav [data-tm-action="search"]');const input=document.getElementById('tm-search-input') as HTMLInputElement;
    input.value='Channel Two';input.dispatchEvent(new Event('input'));expect(env.searchInputEl.value).toBe('Channel Two');expect(originalRender).toHaveBeenCalled();
  });
  it('connects filters, equalizer and quality to the shared engine',()=>{
    click('#tv-mobile-shell [data-tm-action="more"]');click('[data-tm-action="filters"]');
    expect(document.querySelector('[data-tm-filter="language"]')).not.toBeNull();click('[data-tm-action="close-sheet"]');
    click('#tv-mobile-shell [data-tm-action="more"]');click('[data-tm-action="equalizer"]');click('[data-tm-eq="voice"]');expect(env.applyAudioPreset).toHaveBeenCalledWith('voice');
    click('#tv-mobile-shell [data-tm-action="more"]');click('[data-tm-action="quality"]');click('[data-tm-quality="-1"]');expect(env.setHlsQualityLevel).toHaveBeenCalledWith(-1);
  });
});

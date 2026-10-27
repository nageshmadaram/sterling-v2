import { useExperienceOptions, readExperienceOptions } from '../store/useExperienceOptions';
import { create } from 'zustand';
import { api } from '../utils/api';
import { isPreferenceKey, MEDIA_PREFERENCE_KEYS } from './preferenceKeys';
import { useStore } from '../store/useStore';
import { useKiteSettings } from '../store/useKiteSettings';
import { useTickerPins } from '../store/useTickerPins';
import { useMobileExperience } from '../store/useMobileExperience';
import { useViewStore } from '../store/useViewStore';
import { useAppearance, DEFAULT_APPEARANCE, validateAppearance } from './store';
import { useReplayStore, loadPrefs, initialDraft } from '../hooks/useReplayStore';
import { DEFAULT_DENSITY, DENSITY_ORDER } from '../utils/viewportScale';
type Values = Record<string, string | null>;
type Response = { values: Values; initialized: boolean };
const URL = '/api/v1/auth/preferences';
const OWNER = 'sterling.preferences.owner.v1';
let owner = '';
let pending: Values = {};
let busy = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let retry: ReturnType<typeof setInterval> | undefined;
let installed = false;
let generation = 0;
let hydrating = false;
const originalSet = Storage.prototype.setItem;
const originalRemove = Storage.prototype.removeItem;
export const usePreferenceSync = create<{ state: 'loading'|'saved'|'pending'|'error'; error: string }> (()=>({state:'loading',error:''}));
const outbox = (uid:string) => `sterling.preferences.pending.v1.${uid}`;
function snapshot(): Values { const values:Values={}; for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i)!;if(isPreferenceKey(key))values[key]=localStorage.getItem(key);} return values; }
function uiState() { const s=useStore.getState();return {selectedUnderlying:s.selectedUnderlying,tradingMode:s.tradingMode,routerMode:s.routerMode,theme:s.theme,appMode:s.appMode,engineMode:s.engineMode,sterlingV2:s.sterlingV2,zoomLevel:s.zoomLevel,density:s.density,autoFitDensity:s.autoFitDensity}; }
function queue(key:string,value:string|null) { if(!owner||hydrating||!isPreferenceKey(key))return;pending[key]=value;originalSet.call(localStorage,outbox(owner),JSON.stringify(pending));usePreferenceSync.setState({state:'pending',error:''});clearTimeout(timer);timer=setTimeout(()=>void flushPreferences(),250); }
function install() {
 if(installed)return;installed=true;
 Storage.prototype.setItem=function(key,value){originalSet.call(this,key,value);if(this===localStorage)queue(String(key),String(value));};
 Storage.prototype.removeItem=function(key){originalRemove.call(this,key);if(this===localStorage)queue(String(key),null);};
 useStore.subscribe(()=>{if(owner&&!hydrating)localStorage.setItem('sterling_ui_state',JSON.stringify(uiState()));});
 window.addEventListener('pagehide',()=>void flushPreferences());
 window.addEventListener('online',()=>void flushPreferences());
}
export async function flushPreferences() {
 if(!owner||busy||!Object.keys(pending).length)return;
 const uid=owner, batch={...pending};busy=true;
 try { await api.patch<Response>(URL,{values:batch});
  if(owner===uid){for(const [key,value] of Object.entries(batch))if(pending[key]===value)delete pending[key];originalSet.call(localStorage,outbox(uid),JSON.stringify(pending));usePreferenceSync.setState({state:Object.keys(pending).length?'pending':'saved',error:''});}
 }catch {if(owner===uid)usePreferenceSync.setState({state:'error',error:'Preferences not yet saved to database. Retrying; your changes remain queued locally.'});}
 finally {busy=false;if(owner===uid&&Object.keys(pending).length&&usePreferenceSync.getState().state!=='error')timer=setTimeout(()=>void flushPreferences(),250);}
}
export function stopPreferenceSync(){generation++;owner='';clearTimeout(timer);clearInterval(retry);}
async function hydrateStores() {
 useExperienceOptions.setState({options:readExperienceOptions()});
 const kiteRaw=localStorage.getItem('kite-settings');useKiteSettings.setState(useKiteSettings.getInitialState());if(kiteRaw!==null)originalSet.call(localStorage,'kite-settings',kiteRaw);else originalRemove.call(localStorage,'kite-settings');await useKiteSettings.persist.rehydrate();
 const pinsRaw=localStorage.getItem('sterling.kite.tickerPins.v1');useTickerPins.setState(useTickerPins.getInitialState());if(pinsRaw!==null)originalSet.call(localStorage,'sterling.kite.tickerPins.v1',pinsRaw);else originalRemove.call(localStorage,'sterling.kite.tickerPins.v1');await useTickerPins.persist.rehydrate();
 const read=(key:string,fallback:string)=>localStorage.getItem(key)??fallback;
 let saved:Record<string,unknown>={};try{saved=JSON.parse(read('sterling_ui_state','{}'));}catch{/* defaults */}
 const s=useStore.getState();
 s.setSelectedUnderlying(typeof saved.selectedUnderlying==='string'?saved.selectedUnderlying:read('sterling_underlying','NIFTY'));
 const theme=saved.theme??read('sterling_theme','light');s.setTheme(theme==='dark'||theme==='grey'?theme:'light');
 s.setAppMode((saved.appMode??read('sterling_app_mode','basic'))==='pro'?'pro':'basic');
 s.setEngineMode((saved.engineMode??read('sterling_engine_mode','sterling'))==='grok'?'grok':'sterling');
 s.setSterlingV2((saved.sterlingV2??(read('sterling_v2_enabled','false')==='true'))===true);
 s.setZoomLevel(Number(saved.zoomLevel??read('sterling_zoom','1'))||1);
 const density=saved.density??read('sterling_density',DEFAULT_DENSITY);s.setDensity(DENSITY_ORDER.includes(density as typeof DEFAULT_DENSITY)?density as typeof DEFAULT_DENSITY:DEFAULT_DENSITY);
 s.setAutoFitDensity((saved.autoFitDensity??(read('sterling_autofit','false')==='true'))===true);
 s.setTradingMode(typeof saved.tradingMode==='string'?saved.tradingMode:'swing');s.setRouterMode(typeof saved.routerMode==='string'?saved.routerMode:'live');
 useViewStore.setState({view:read('sterling.view','live')==='simulation'?'simulation':'live'});
 useMobileExperience.setState({style:read('sterling_mobile_style','app')==='full'?'full':'app'});
 try{useAppearance.getState().replace(validateAppearance(JSON.parse(read('sterling_appearance_v1','{}'))));}catch{useAppearance.getState().replace(DEFAULT_APPEARANCE);}
 const prefs=loadPrefs();useReplayStore.setState({open:prefs.open,mode:prefs.mode,prevMode:prefs.mode,height:prefs.height,tab:prefs.tab,draft:initialDraft()});
}
export async function startPreferenceSync(uid:string) {
 stopPreferenceSync();const run=generation;install();usePreferenceSync.setState({state:'loading',error:''});
 const prior=localStorage.getItem(OWNER);
 const mediaFits=(key:string,value:unknown)=>typeof value==='string'&&new Blob([value]).size<=(key.startsWith('sterling.tv.')?500000:200000);
 // Back up the current browser's preferences before changing ownership/restoring.
 if(prior)originalSet.call(localStorage,`sterling.preferences.backup.v1.${prior}`,JSON.stringify(snapshot()));
 let result=await api.get<Response>(URL);if(run!==generation)return;
 const migratable=Object.fromEntries(Object.entries(snapshot()).filter(([key,value])=>!MEDIA_PREFERENCE_KEYS.has(key)||mediaFits(key,value)));
 if(!result.initialized){const initial=(!prior||prior===uid)?migratable:{};result=await api.patch<Response>(URL,{values:initial,initialize_only:true});}
 if(run!==generation)return;
 // Migrate this owner's existing media library once, including accounts that
 // already had appearance preferences before media sync was introduced.
 if(!prior||prior===uid){
  const media=Object.fromEntries(Object.entries(migratable).filter(([key])=>MEDIA_PREFERENCE_KEYS.has(key)&&!(key in result.values)));
  // Unowned v13 TV keys cannot be attributed to the current account.
  // Preserve them for explicit recovery, but never restore them automatically.
  if(Object.keys(media).length){result=await api.patch<Response>(URL,{values:media});if(run!==generation)return;}
 }
 let queued:Values={};try{queued=JSON.parse(localStorage.getItem(outbox(uid))??'{}');}catch{/* corrupt outbox */}
 queued=Object.fromEntries(Object.entries(queued).filter(([key,value])=>isPreferenceKey(key)&&(typeof value==='string'||value===null)&&(!MEDIA_PREFERENCE_KEYS.has(key)||value===null||mediaFits(key,value))));
 if(Object.keys(queued).length) {const r=await api.patch<Response>(URL,{values:queued});result=r;originalRemove.call(localStorage,outbox(uid));}
 if(run!==generation)return;
 hydrating=true;
 try {
  for(const key of Object.keys(snapshot()))originalRemove.call(localStorage,key);
  for(const [key,value] of Object.entries(result.values))if(isPreferenceKey(key)&&value!==null)originalSet.call(localStorage,key,value);
  await hydrateStores();originalSet.call(localStorage,OWNER,uid);
 }finally{hydrating=false;}
 if(run!==generation)return;
 owner=uid;pending={};window.dispatchEvent(new Event('sterling-preferences-hydrated'));usePreferenceSync.setState({state:'saved',error:''});retry=setInterval(()=>void flushPreferences(),5000);
 // Persist validated defaults/migrations generated during hydration as well.
 const after=snapshot();for(const [key,value] of Object.entries(after))if(result.values[key]!==value)queue(key,value);
}

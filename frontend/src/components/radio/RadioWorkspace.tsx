import { readMediaCommand, readMediaSuggestionResponse, type MediaCommand } from '../media/mediaBridge';
import { useCallback, useEffect, useRef, useState } from 'react';

const RADIO_KEYS = [
  'omnicast_v11_config',
  'omnicast_v11_recents',
  'omnicast_v11_counts',
  'omnicast_v11_liked',
  'omnicast_v11_extras',
] as const;
const ALLOWED_KEYS = new Set<string>(RADIO_KEYS);
const STORAGE_PREFIX = 'sterling.radio.';
const MAX_VALUE_LENGTH = 50_000;
const READY = 'sterling.radio.ready';
const HYDRATE = 'sterling.radio.hydrate';
const SAVE = 'sterling.radio.save';

function readRadioPrefs() {
  const values: Record<string,string> = {};
  for (const key of RADIO_KEYS) {
    try {
      const value = window.localStorage.getItem(STORAGE_PREFIX + key);
      if (value !== null && value.length <= MAX_VALUE_LENGTH) values[key] = value;
    } catch { /* private mode: Radio remains usable without persistence */ }
  }
  return values;
}

/**
 * The Radio document executes inside an opaque-origin iframe, preventing
 * inherited access to Sterling's DOM, auth cookies, Kite API or storage.
 * Only four isolated Radio keys are accepted through the message bridge.
 */
export function RadioWorkspace({onState,onPlayerState}:{onState?:(state:{theme:'spotify'|'youtube'|'netflix';query:string})=>void;onPlayerState?:(expanded:boolean)=>void}) {
  const stateRef = useRef(onState);
  stateRef.current = onState;
  const playerStateRef=useRef(onPlayerState);
  playerStateRef.current=onPlayerState;
  const commandsRef = useRef<MediaCommand[]>([]);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const hydratedRef = useRef(false);
  const readyRef = useRef(false);
  const [version,setVersion] = useState(0);
  const [failed,setFailed] = useState(false);
  const [loaded,setLoaded] = useState(false);

  const hydrate = useCallback(() => {
    const frame = frameRef.current;
    if (!frame?.contentWindow) return;
    frame.contentWindow.postMessage({type:HYDRATE,hostHeader:true,values:readRadioPrefs()}, '*');
    hydratedRef.current = true;
  },[]);

  useEffect(() => {
    const onMessage = (event:MessageEvent) => {
      const frame = frameRef.current;
      if (!frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
      const message = event.data;
      if (!message || typeof message !== 'object') return;
      if (message.type === READY) {
        readyRef.current = true;
        setLoaded(true);
        setFailed(false);
        hydrate();
        frame.contentWindow?.postMessage({type:'sterling.radio.ack'}, '*');
        commandsRef.current.splice(0).forEach(command => frame.contentWindow?.postMessage({type:'sterling.radio.command',...command},'*'));
      } else if (message.type === 'sterling.radio.suggestions') {
        const response=readMediaSuggestionResponse(message);
        if(response)window.dispatchEvent(new CustomEvent('sterling-radio-suggestions',{detail:response}));
      } else if (message.type === 'sterling.radio.state') {
        if ((message.theme === 'spotify' || message.theme === 'youtube' || message.theme === 'netflix') && typeof message.query === 'string' && message.query.length <= 200)
          stateRef.current?.({theme:message.theme,query:message.query});
      } else if(message.type === 'sterling.radio.player-state') {
        if(typeof message.expanded==='boolean')playerStateRef.current?.(message.expanded);
      } else if (message.type === 'sterling.radio.focus-search') {
        window.dispatchEvent(new Event('sterling-radio-focus-search'));
      } else if (message.type === SAVE && hydratedRef.current) {
        if (!ALLOWED_KEYS.has(message.key) ||
          (message.value !== null &&
            (typeof message.value !== 'string' || message.value.length > MAX_VALUE_LENGTH))) return;
        try {
          if (message.value === null) window.localStorage.removeItem(STORAGE_PREFIX + message.key);
          else window.localStorage.setItem(STORAGE_PREFIX + message.key, message.value);
        } catch { /* memory-only Radio session if storage unavailable */ }
      }
    };
    window.addEventListener('message',onMessage);
    const refreshPreferences=()=>{hydratedRef.current=false;readyRef.current=false;setLoaded(false);setFailed(false);setVersion(n=>n+1);};
    window.addEventListener('sterling-preferences-hydrated',refreshPreferences);
    const watchdog = window.setTimeout(() => {
      if (!readyRef.current) setFailed(true);
    }, 15000);
    return () => {window.clearTimeout(watchdog);window.removeEventListener('message',onMessage);window.removeEventListener('sterling-preferences-hydrated',refreshPreferences);};
  },[version,hydrate]);

  useEffect(() => {
    const reload=() => {hydratedRef.current=false;readyRef.current=false;setLoaded(false);setFailed(false);setVersion(n=>n+1);};
    const command=(event:Event) => {
      const message=readMediaCommand('radio',(event as CustomEvent).detail);
      if(!message)return;
      if(readyRef.current)frameRef.current?.contentWindow?.postMessage({type:'sterling.radio.command',...message},'*');
      else {commandsRef.current=commandsRef.current.filter(item=>item.action!==message.action);commandsRef.current.push(message);}
    };
    window.addEventListener('sterling-radio-command',command);
    window.addEventListener('sterling-radio-reload',reload);
    return () => {window.removeEventListener('sterling-radio-reload',reload);window.removeEventListener('sterling-radio-command',command);};
  },[]);

  return <section aria-label="Sterling Radio workspace" data-testid="sterling-radio-workspace"
    style={{position:'relative',display:'flex',flex:1,minWidth:0,minHeight:0,overflow:'hidden',background:'#101923'}}>
    {!loaded && !failed && <div role="status" style={{position:'absolute',inset:0,display:'grid',placeItems:'center',color:'#fff',pointerEvents:'none'}}>Loading Radio…</div>}
    {failed && <div role="alert" style={{position:'absolute',inset:0,display:'grid',placeItems:'center',zIndex:1,background:'#101923',color:'#fff'}}>
      <div><p>Radio could not load. Check network access or retry.</p>
        <button type="button" onClick={() => {hydratedRef.current=false;readyRef.current=false;setLoaded(false);setFailed(false);setVersion(n=>n+1);}}>Retry Radio</button>
      </div>
    </div>}
    <iframe key={version} ref={frameRef}
      title="Sterling Radio player" data-testid="sterling-radio-frame"
      src="/radio/index.html"
      sandbox="allow-scripts allow-forms allow-downloads allow-presentation"
      referrerPolicy="no-referrer"
      allow="autoplay; encrypted-media" loading="eager"
      onLoad={hydrate}
      onError={() => setFailed(true)}
      style={{border:0,width:'100%',height:'100%',flex:1,minWidth:0,minHeight:0,background:'#101923'}} />
  </section>;
}

import React, { useCallback, useEffect, useRef, useState } from 'react';

/**
 * IPTV runs in an opaque-origin sandbox. The TV application's inline player
 * scripts and third-party playlists MUST NOT share Sterling's origin/storage,
 * auth cookies, bearer tokens, broker APIs, or DOM.
 *
 * Only these eight TV preferences cross the boundary; messages are accepted
 * exclusively from the mounted frame and contain no trading information.
 */
export const TV_STORAGE_KEYS = [
  'streamos_sources_v14',
  'streamos_source_mode_v14',
  'streamos_favs_v14',
  'streamos_stats_v14',
  'streamos_history_v14',
  'streamos_startup_channel_v14',
  'streamos_theme_v14',
  'streamos_ui_v14',
] as const;
type TvKey = (typeof TV_STORAGE_KEYS)[number];

const MESSAGE_READY = 'sterling.tv.ready';
const MESSAGE_HYDRATE = 'sterling.tv.hydrate';
const MESSAGE_SAVE = 'sterling.tv.save';
const STORAGE_PREFIX = 'sterling.tv.';
const MAX_VALUE_LENGTH = 500_000;
const ALLOWED_KEYS = new Set<string>(TV_STORAGE_KEYS);

function readTvPreferences(): Partial<Record<TvKey, string>> {
  const values: Partial<Record<TvKey, string>> = {};
  for (const key of TV_STORAGE_KEYS) {
    try {
      let value = window.localStorage.getItem(STORAGE_PREFIX + key);
      // V14 renamed the persisted TV keys. Preserve existing v13 playlists,
      // subscriptions and watch history without ever sharing trading storage.
      if (!value && key !== 'streamos_startup_channel_v14') {
        const legacy = window.localStorage.getItem(
          STORAGE_PREFIX + key.replace(/_v14$/, '_v13'));
        if (legacy && legacy.length <= MAX_VALUE_LENGTH) {
          value = legacy;
          window.localStorage.setItem(STORAGE_PREFIX + key, legacy);
        }
      }
      if (value && value.length <= MAX_VALUE_LENGTH) values[key] = value;
    } catch { /* storage can be unavailable in a private browser */ }
  }
  return values;
}

export type TvHeaderState = {theme:'youtube'|'spotify'|'netflix';query:string};
export function TvWorkspace({onState}:{onState?:(value:TvHeaderState)=>void} = {}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const readyRef = useRef(false);
  const pendingCommands=useRef(new Map<string,Record<string,unknown>>());
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const onLoad = useCallback(() => {
    // Mobile WebKit can finish frame navigation before its one-shot "ready"
    // message reaches React's effect listener. The child repeats "ready"
    // until hydration; this extra onLoad nudge covers that race too.
    const frame = ref.current;
    if (frame?.contentWindow) frame.contentWindow.postMessage({
      type: MESSAGE_HYDRATE, values: readTvPreferences(), hostHeader:true,
    }, '*');
  }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frame = ref.current;
      if (!frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
      const data = event.data;
      if (!data || typeof data !== 'object' || typeof data.type !== 'string') return;
      if (data.type === MESSAGE_READY) {
        readyRef.current = true;
        setLoaded(true);
        setFailed(false);
        frame.contentWindow?.postMessage({
          type: MESSAGE_HYDRATE,
          values: readTvPreferences(), hostHeader:true,
        }, '*'); // opaque-origin sandbox requires "*" for targetOrigin
        for(const command of pendingCommands.current.values())frame.contentWindow?.postMessage(command,'*');
        pendingCommands.current.clear();
      } else if(data.type === 'sterling.tv.focus-search') {
        window.dispatchEvent(new Event('sterling-tv-focus-search'));
      } else if(data.type === 'sterling.tv.state') {
        if(['youtube','spotify','netflix'].includes(data.theme) && typeof data.query==='string' && data.query.length<=200)onState?.({theme:data.theme,query:data.query});
      } else if (data.type === MESSAGE_SAVE) {
        // Startup playback emits play-history writes before hydration. Never
        // overwrite persisted preferences with temporary boot defaults.
        if (!readyRef.current) return;
        if (!ALLOWED_KEYS.has(data.key) ||
            typeof data.value !== 'string' ||
            data.value.length > MAX_VALUE_LENGTH) return;
        try { window.localStorage.setItem(STORAGE_PREFIX + data.key, data.value); }
        catch { /* browsing remains functional even with disabled storage */ }
      }
    };
    // A loaded iframe is not proof its scripts started. Make a blocked CDN
    // or mobile WebKit failure visible, with a TV-only retry path.
    const watchdog = window.setTimeout(() => {
      if (!readyRef.current) setFailed(true);
    }, 15000);
    window.addEventListener('message', onMessage);
    const command=(event:Event)=>{
      const data=(event as CustomEvent).detail;
      if(!data||!['theme','search','more','play-search'].includes(data.action))return;
      if(data.action==='theme'&&!['youtube','spotify','netflix'].includes(data.value))return;
      if(data.action==='search'&&(typeof data.value!=='string'||data.value.length>200))return;
      const payload={type:'sterling.tv.command',action:data.action,value:data.value};
      if(!readyRef.current){pendingCommands.current.set(data.action,payload);return;}
      ref.current?.contentWindow?.postMessage(payload,'*');
    };
    window.addEventListener('sterling-tv-command',command);
    return () => { window.clearTimeout(watchdog);window.removeEventListener('message',onMessage);window.removeEventListener('sterling-tv-command',command); };
  }, [reloadKey,onState]);

  return (
    <section
      aria-label="Sterling TV workspace"
      data-testid="sterling-tv-workspace"
      style={{ position: 'relative', flex: 1, minWidth: 0, minHeight: 0,
        display: 'flex', overflow: 'hidden', background: '#0f0f0f' }}
    >
      {!loaded && !failed && (
        <div role="status" style={{ position: 'absolute', inset: 0, display: 'grid',
          placeItems: 'center', color: '#ededed', fontSize: 13, pointerEvents:'none' }}>
          Loading TV…
        </div>
      )}
      {failed && (
        <div role="alert" style={{ position: 'absolute', inset: 0, display: 'grid',
          placeItems: 'center', color: '#ededed', fontSize: 13 }}>
          <div style={{ textAlign: 'center', padding: 16 }}>
            <p>TV did not initialize. Check the network or retry this player.</p>
            <button type="button" onClick={() => {
              readyRef.current = false;
              setLoaded(false);
              setFailed(false);
              setReloadKey(n => n + 1);
            }} style={{ cursor: 'pointer', marginTop: 12, padding: '8px 16px',
              border: '1px solid #888', color: '#fff', background: '#222',
              borderRadius: 6 }}>Retry TV</button>
          </div>
        </div>
      )}
      <iframe
        key={reloadKey}
        ref={ref}
        title="Sterling TV player"
        data-testid="sterling-tv-frame"
        src="/tv/index.html"
        loading="eager"
        allow="autoplay; fullscreen; picture-in-picture; encrypted-media"
        allowFullScreen
        sandbox="allow-scripts allow-forms allow-downloads allow-presentation"
        referrerPolicy="no-referrer"
        onLoad={onLoad}
        onError={() => setFailed(true)}
        style={{ display: 'block', border: 0, width: '100%', height: '100%',
          flex: 1, minWidth: 0, minHeight: 0, background: '#0f0f0f' }}
      />
    </section>
  );
}

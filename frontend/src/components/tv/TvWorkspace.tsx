import React, { useCallback, useEffect, useRef, useState } from 'react';

/**
 * IPTV runs in an opaque-origin sandbox. The TV application's inline player
 * scripts and third-party playlists MUST NOT share Sterling's origin/storage,
 * auth cookies, bearer tokens, broker APIs, or DOM.
 *
 * Only these five TV preferences cross the boundary; messages are accepted
 * exclusively from the mounted frame and contain no trading information.
 */
export const TV_STORAGE_KEYS = [
  'streamos_sources_v13',
  'streamos_source_mode_v13',
  'streamos_favs_v13',
  'streamos_stats_v13',
  'streamos_history_v13',
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
      const value = window.localStorage.getItem(STORAGE_PREFIX + key);
      if (value && value.length <= MAX_VALUE_LENGTH) values[key] = value;
    } catch { /* storage can be unavailable in a private browser */ }
  }
  return values;
}

export function TvWorkspace() {
  const ref = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  const onLoad = useCallback(() => {
    setLoaded(true);
    // In a sandboxed opaque-origin frame, the contentWindow is available
    // but the DOM is deliberately inaccessible. The child announces readiness
    // after its standalone app has initialized.
  }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frame = ref.current;
      if (!frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
      const data = event.data;
      if (!data || typeof data !== 'object' || typeof data.type !== 'string') return;
      if (data.type === MESSAGE_READY) {
        frame.contentWindow?.postMessage({
          type: MESSAGE_HYDRATE,
          values: readTvPreferences(),
        }, '*'); // opaque-origin sandbox requires "*" for targetOrigin
      } else if (data.type === MESSAGE_SAVE) {
        if (!ALLOWED_KEYS.has(data.key) ||
            typeof data.value !== 'string' ||
            data.value.length > MAX_VALUE_LENGTH) return;
        try { window.localStorage.setItem(STORAGE_PREFIX + data.key, data.value); }
        catch { /* browsing remains functional even with disabled storage */ }
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  return (
    <section
      aria-label="Sterling TV workspace"
      data-testid="sterling-tv-workspace"
      style={{ position: 'relative', flex: 1, minWidth: 0, minHeight: 0,
        display: 'flex', overflow: 'hidden', background: '#0f0f0f' }}
    >
      {!loaded && !failed && (
        <div role="status" style={{ position: 'absolute', inset: 0, display: 'grid',
          placeItems: 'center', color: '#ededed', fontSize: 13 }}>
          Loading TV…
        </div>
      )}
      {failed && (
        <div role="alert" style={{ position: 'absolute', inset: 0, display: 'grid',
          placeItems: 'center', color: '#ededed', fontSize: 13 }}>
          TV could not load. Select KITE and return to TV to retry.
        </div>
      )}
      <iframe
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

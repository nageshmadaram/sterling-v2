import { useEffect, useState } from 'react';

/**
 * Radio is an untrusted media application, not a Kite/Simulation execution pane.
 * Opaque-origin isolation prevents access to the Sterling DOM, cookies,
 * authenticated API origin, broker credentials and localStorage.
 * Public app assets live at frontend/public/radio/index.html.
 */
export function RadioWorkspace() {
  const [version, setVersion] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const reload = () => { setFailed(false); setVersion(n => n + 1); };
    window.addEventListener('sterling-radio-reload', reload);
    return () => window.removeEventListener('sterling-radio-reload', reload);
  }, []);

  return <section aria-label="Sterling Radio workspace" data-testid="sterling-radio-workspace"
    style={{position:'relative',display:'flex',flex:1,minWidth:0,minHeight:0,overflow:'hidden',background:'#101923'}}>
    {failed && <div role="alert" style={{position:'absolute',inset:0,display:'grid',placeItems:'center',zIndex:1,background:'#101923',color:'#fff'}}>
      <div><p>Radio could not load. Check the Radio app files or network.</p>
        <button type="button" onClick={() => {setFailed(false);setVersion(n => n + 1);}}>Retry Radio</button>
      </div>
    </div>}
    <iframe key={version} title="Sterling Radio player" data-testid="sterling-radio-frame"
      src="/radio/index.html"
      sandbox="allow-scripts allow-forms allow-downloads allow-presentation"
      referrerPolicy="no-referrer"
      allow="autoplay; encrypted-media" loading="eager"
      onError={() => setFailed(true)}
      style={{border:0,width:'100%',height:'100%',flex:1,minWidth:0,minHeight:0,background:'#101923'}} />
  </section>;
}

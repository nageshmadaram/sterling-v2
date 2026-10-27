import { useViewStore, type AppView } from '../store/useViewStore';
import { useSterlingProfile } from '../components/kite/ProfileMenu';
import { useExperienceOptions, orderedNavigation, NAV_OPTIONS } from '../store/useExperienceOptions';
import { groupPrimaryNavigation } from '../components/kite/navigationDensity';
import React, { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { SimpleSettingsDrawer } from '../components/SimpleSettings';
import { KiteTab } from '../components/kite/KiteTab';
import { TvWorkspace } from '../components/tv/TvWorkspace';
import { RadioWorkspace } from '../components/radio/RadioWorkspace';
import { MediaHeaderControls, type MediaTheme } from '../components/media/MediaHeaderControls';
import type { NavItem } from '../components/kite/KiteLayout';
import { useKiteSettings } from '../store/useKiteSettings';
import { useSavedSelection } from '../hooks/useSavedSelection';
import { hasUnsavedDraft } from '../components/kite/config/unsavedDraftGuard';
import { SterlingLogo } from '../components/SterlingLogo';
import { SpectrumNavigation } from '../components/sim/SpectrumNavigation';
import { SimBanner } from '../components/sim/SimBanner';
import '../styles/terminal.css';
import { MobileExperienceMenu } from '../components/kite/MobileExperienceMenu';
import { KiteSessionBar } from '../components/kite/board/KiteSessionBar';
import { useMobileExperience } from '../store/useMobileExperience';
import { useMobileBackLayer } from '../hooks/useMobileBackLayer';
import { useSimStatus, isOwnerDenied } from '../hooks/useSim';

import { notificationDestination } from '../lib/notificationDestination';

export function SimpleTerminal({ kiteOnly = false }: { kiteOnly?: boolean } = {}) {
  const mobileStyle = useMobileExperience(s => s.style);
  useEffect(() => { document.body.dataset.mobileStyle = mobileStyle; return () => { delete document.body.dataset.mobileStyle; }; }, [mobileStyle]);
  useEffect(()=>{const params=new URLSearchParams(window.location.search);if(params.get('view')==='live'&&notificationDestination(params.get('notification')))useViewStore.getState().setView('live');},[]);
  const [showSettings, setShowSettings] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [spectrum, setSpectrum] = useState<'kite' | 'tv' | 'radio'>(() => {
    if (kiteOnly) return 'kite';
    const params = new URLSearchParams(window.location.search);
    if (params.get('view') === 'live' && notificationDestination(params.get('notification'))) return 'kite';
    const home = useExperienceOptions.getState().options.spectrumHome;
    return home === 'tv' || home === 'radio' ? home : 'kite';
  });
  const [tvState,setTvState]=useState<{theme:'youtube'|'spotify'|'netflix';query:string}>(()=>{let theme='youtube';try{theme=localStorage.getItem('sterling.tv.streamos_theme_v14')||theme;}catch{}return {theme:theme==='spotify'||theme==='netflix'?theme:'youtube',query:''};});
  const [radioState,setRadioState]=useState<{theme:MediaTheme;query:string}>({theme:'spotify',query:''});
  const [tvPlayerExpanded,setTvPlayerExpanded]=useState(false);
  const [radioPlayerExpanded,setRadioPlayerExpanded]=useState(false);
  const onRadioState=useCallback((state:{theme:MediaTheme;query:string})=>setRadioState(state),[]);
  const radioCommand=(action:string,value?:string)=>window.dispatchEvent(new CustomEvent('sterling-radio-command',{detail:{action,value}}));
  const onTvState=useCallback((state:{theme:'youtube'|'spotify'|'netflix';query:string})=>setTvState(state),[]);
  const tvCommand=(action:string,value?:string)=>window.dispatchEvent(new CustomEvent('sterling-tv-command',{detail:{action,value}}));
  // In-app steps inside TV and Radio (a category, Search, a sheet): one phone Back undoes one step.
  // The layer name carries the depth, so each step registers its own history entry.
  const [tvNavDepth,setTvNavDepth]=useState(0);
  const [radioNavDepth,setRadioNavDepth]=useState(0);
  useEffect(()=>{
    const tv=(event:Event)=>setTvNavDepth((event as CustomEvent<number>).detail);
    const radio=(event:Event)=>setRadioNavDepth((event as CustomEvent<number>).detail);
    window.addEventListener('sterling-tv-nav',tv);window.addEventListener('sterling-radio-nav',radio);
    return()=>{window.removeEventListener('sterling-tv-nav',tv);window.removeEventListener('sterling-radio-nav',radio);};
  },[]);
  useMobileBackLayer(spectrum==='tv'&&tvNavDepth>0,()=>tvCommand('back'),`tv-nav-${tvNavDepth}`);
  useMobileBackLayer(spectrum==='radio'&&radioNavDepth>0,()=>radioCommand('back'),`radio-nav-${radioNavDepth}`);
  useMobileBackLayer(spectrum==='tv'&&tvPlayerExpanded,()=>{
    setTvPlayerExpanded(false);tvCommand('collapse');
  },'tv-player');
  useMobileBackLayer(spectrum==='radio'&&radioPlayerExpanded,()=>{
    setRadioPlayerExpanded(false);radioCommand('minimize');
  },'radio-player');
  useEffect(()=>{try{localStorage.setItem('sterling.workspace',spectrum);}catch{}},[spectrum]);
  const selectedView = useViewStore(s => s.view);
  const setSelectedView = useViewStore(s => s.setView);
  const initialHomePendingRef = useRef(true);
  const simStatus = useSimStatus();
  const simulationAllowed = selectedView === 'simulation' || simStatus.isSuccess ||
    (simStatus.isError && !isOwnerDenied(simStatus.error));
  const chooseSpectrum = (next: 'kite' | 'tv' | 'radio', targetView?: AppView) => {
    if (kiteOnly && next !== 'kite') {
      window.dispatchEvent(new CustomEvent('sterling-navigate', { detail: next }));
      return false;
    }
    initialHomePendingRef.current = false;
    if (next === spectrum && (!targetView || targetView === selectedView)) return true;
    if (targetView === 'simulation' && !simulationAllowed) return false;
    if (hasUnsavedDraft() && !window.confirm('You have unsaved settings changes. Switch workspace and discard them?')) return false;
    setToolsOpen(false);
    setShowSettings(false);
    if(next!==spectrum){setTvPlayerExpanded(false);setRadioPlayerExpanded(false);}
    if (targetView && targetView !== selectedView) setSelectedView(targetView);
    setSpectrum(next);
    return true;
  };
  const toolsRef = useRef<HTMLDivElement>(null);
  const preferredSection = useKiteSettings((s) => s.defaultSection);
  const notificationParams = new URLSearchParams(window.location.search);
  const notificationNav = notificationParams.get('view') === 'live' ? notificationDestination(notificationParams.get('notification')) : null;
  const userNavigatedRef = useRef(Boolean(notificationNav) || (() => { try { return Boolean(sessionStorage.getItem('sterling.page.workspace.nav')); } catch { return false; } })());
  const [kiteNav, setKiteNav] = useSavedSelection<NavItem>('workspace.nav', useKiteSettings.getState().defaultSection || 'dashboard', notificationNav);
  const [mobilePaneTitle, setMobilePaneTitle] = useState('');
  useLayoutEffect(() => {
    // Subscribe before child passive effects publish their initial pane title.
    // A new page/workspace must not retain the previous pane or chart label.
    setMobilePaneTitle('');
    const update = (event: Event) => setMobilePaneTitle(String((event as CustomEvent).detail || ''));
    window.addEventListener('kite-mobile-pane-title', update);
    return () => window.removeEventListener('kite-mobile-pane-title', update);
  }, [kiteNav, spectrum]);
  const navOptions=useExperienceOptions(s=>s.options);
  useEffect(() => {
    if (kiteOnly || !initialHomePendingRef.current) return;
    // A live notification must never silently open a simulation workspace.
    if (new URLSearchParams(window.location.search).get('view') === 'live') {
      initialHomePendingRef.current = false;
      return;
    }
    if (navOptions.spectrumHome === 'simulation' && !simulationAllowed) return;
    initialHomePendingRef.current = false;
    setSelectedView(navOptions.spectrumHome === 'simulation' ? 'simulation' : 'live');
  }, [navOptions.spectrumHome, simulationAllowed, setSelectedView]);
  // Settings can hydrate after this component mounts or change in Experience.
  // Never retain a workspace that the saved selection has disabled.
  useEffect(() => {
    if (kiteOnly) return;
    const active = spectrum === 'kite' && selectedView === 'simulation' ? 'simulation' : spectrum;
    if (navOptions.spectrumVisible[active]) return;
    const home = navOptions.spectrumHome;
    setSpectrum(home === 'tv' || home === 'radio' ? home : 'kite');
    setSelectedView(home === 'simulation' && simulationAllowed ? 'simulation' : 'live');
    setToolsOpen(false);
    setShowSettings(false);
  }, [navOptions, spectrum, selectedView, simulationAllowed, setSelectedView]);
  const goHome = () => {
    if (kiteOnly) {
      if (hasUnsavedDraft() && !window.confirm('Discard unsaved settings changes?')) return;
      window.dispatchEvent(new Event('sterling-home'));
      setToolsOpen(false); setShowSettings(false); setSelectedView('live');
      const preferred = useKiteSettings.getState().defaultSection || 'dashboard';
      const section = navOptions.navHidden.includes(preferred) ? (navigation[0]?.id || 'dashboard') : preferred;
      userNavigatedRef.current = true; setKiteNav(section);
      window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: section }));
      return;
    }
    initialHomePendingRef.current = false;
    // Close managed overlays before changing the underlying workspace.
    window.dispatchEvent(new Event('sterling-home'));
    tvCommand('collapse');radioCommand('minimize');
    setTvPlayerExpanded(false);setRadioPlayerExpanded(false);
    setToolsOpen(false);
    setShowSettings(false);
    const home = navOptions.spectrumHome;
    const nextView: AppView = home === 'simulation' ? 'simulation' : 'live';
    if (home === 'simulation' && !simulationAllowed) return;
    if (!chooseSpectrum(home === 'tv' || home === 'radio' ? home : 'kite', home === 'tv' || home === 'radio' ? undefined : nextView)) return;
    if (home === 'kite' || home === 'simulation') {
      userNavigatedRef.current = true;
      const section = useKiteSettings.getState().defaultSection || 'dashboard';
      setKiteNav(section);
      window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: section }));
    }
  };
  // The STERLING logo opens KITE whenever KITE is enabled; the configured Home (which may be
  // TV or Radio) still decides where the app starts and where the page close control returns.
  const openFromLogo = () => {
    if (kiteOnly) { window.dispatchEvent(new CustomEvent('sterling-select-workspace')); return; }
    if (!navOptions.spectrumVisible.kite) { goHome(); return; }
    initialHomePendingRef.current = false;
    window.dispatchEvent(new Event('sterling-home'));
    tvCommand('collapse');radioCommand('minimize');
    setTvPlayerExpanded(false);setRadioPlayerExpanded(false);
    setToolsOpen(false);
    setShowSettings(false);
    if (!chooseSpectrum('kite', 'live')) return;
    userNavigatedRef.current = true;
    const section = useKiteSettings.getState().defaultSection || 'dashboard';
    setKiteNav(section);
    window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: section }));
  };
  useEffect(() => {
    // KITE's own Home tab stays in KITE; the configured Home (maybe TV or Radio) is for app-level Home.
    window.addEventListener('sterling-open-kite', goHome);
    return () => window.removeEventListener('sterling-open-kite', goHome);
  });
  useEffect(() => {
    window.addEventListener('sterling-go-home', goHome);
    return () => window.removeEventListener('sterling-go-home', goHome);
  });
  const {data:sterlingProfile}=useSterlingProfile();
  const navigation = orderedNavigation(NAV_OPTIONS.map(([id,label])=>({id,label})), navOptions);
  const { primary, overflow } = groupPrimaryNavigation(navigation);
  useEffect(() => {
    if (!navOptions.navHidden.includes(kiteNav)) return;
    const next = navigation.find(item=>item.id===preferredSection)?.id || navigation[0]?.id || 'dashboard';
    setKiteNav(next);
    window.dispatchEvent(new CustomEvent('kite-nav-click',{detail:next}));
  }, [kiteNav, navOptions, preferredSection]);

  useEffect(() => {
    if (!toolsOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!toolsRef.current?.contains(event.target as Node)) setToolsOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setToolsOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [toolsOpen]);

  useEffect(() => {
    if (!userNavigatedRef.current && preferredSection && preferredSection !== kiteNav) {
      setKiteNav(preferredSection);
    }
  }, [preferredSection, kiteNav]);

  const handleKiteNav = (nav: NavItem) => {
    if (navOptions.navHidden.includes(nav)) return;
    userNavigatedRef.current = true;
    if (nav !== kiteNav && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('kite-scroll-to-draft-bar'));
      if (!window.confirm('You have unsaved settings changes. Leave this page and discard them?')) {
        return;
      }
    }
    if (spectrum !== 'kite') setSpectrum('kite');
    setToolsOpen(false);
    setKiteNav(nav);
    window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: nav }));
  };

  useEffect(() => {
    const onNav = (event: Event) => {
      const next = (event as CustomEvent<NavItem>).detail;
      if (typeof next === 'string' && !useExperienceOptions.getState().options.navHidden.includes(next)) {
        userNavigatedRef.current = true;
        setKiteNav(next);
      }
    };
    window.addEventListener('kite-nav-click', onNav);
    window.addEventListener('kite-page-changed', onNav);
    return () => { window.removeEventListener('kite-nav-click', onNav); window.removeEventListener('kite-page-changed', onNav); };
  }, []);

  useEffect(()=>{if(!('serviceWorker' in navigator))return;const receive=(event:MessageEvent)=>{if(event.data?.type!=='sterling-notification')return;try{const url=new URL(event.data.url,window.location.origin);if(url.origin!==window.location.origin)return;const next=notificationDestination(url.searchParams.get('notification'));if(next){if(hasUnsavedDraft()&&!window.confirm('Open this live-account notification and discard unapplied settings?'))return;useViewStore.getState().setView('live');window.location.assign(url.href);}}catch{/* malformed notification */}};navigator.serviceWorker.addEventListener('message',receive);return()=>navigator.serviceWorker.removeEventListener('message',receive);},[kiteNav]);


  const configuredHome = navOptions.spectrumHome;
  const isConfiguredHome =
    configuredHome === 'tv'
      ? spectrum === 'tv'
      : configuredHome === 'radio'
        ? spectrum === 'radio'
        : configuredHome === 'simulation'
          ? spectrum === 'kite' &&
            selectedView === 'simulation' &&
            kiteNav === preferredSection
          : spectrum === 'kite' &&
            selectedView !== 'simulation' &&
            kiteNav === preferredSection;

  return (
    <div className={`term-root ${spectrum==='tv'?'sterling-tv-mode':spectrum==='radio'?'sterling-radio-mode':''}`} data-tv-theme={tvState.theme} data-radio-theme={radioState.theme}>
      <style>{`

        .sterling-mobile-close-page { display: none; }
        @media (max-width: 768px) {
          .sterling-mobile-close-page {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            width: 34px;
            height: 34px;
            margin-left: 8px;
            border: 1px solid var(--t-border);
            border-radius: 8px;
            color: var(--t-bright);
            background: var(--k-bg);
            cursor: pointer;
            touch-action: manipulation;
          }
        }
        .kite-header-actions > * { flex-shrink: 0; }
        .kite-topnav {
          display: flex; align-items: center; gap: 6px; height: 100%;
          min-width: 0; flex-shrink: 1;
          overflow-x: auto; overflow-y: hidden;
          scrollbar-width: none; -ms-overflow-style: none;
        }
        .kite-topnav::-webkit-scrollbar { display: none; }
        .kite-topnav > button { flex: 0 0 auto; white-space: nowrap; }
      `}</style>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="sterling-header" style={{
        flexShrink: 0,
        borderBottom: '1px solid var(--t-border)', position: 'relative', zIndex: 20,
      }}>
        {/* Original KITE header and mode switch; TV uses the same button style. */}
        <div className="sterling-header-row" style={{
          display: 'flex', alignItems: 'center', gap: 0,
          height: 44, padding: '0 20px',
        }}>
          <button type="button" className="sterling-home-button" aria-label="Sterling Home" title="Choose workspace"
            onClick={openFromLogo} style={{ display: 'inline-flex', alignItems: 'center', gap: 9, marginRight: 16, userSelect: 'none',
              background: 'none', border: 0, padding: 0, cursor: 'pointer', color: 'inherit', flexShrink: 0 }}>
            <SterlingLogo size={24} />
            <span style={{
              fontSize: 17, fontWeight: 800, letterSpacing: '0.18em',
              color: 'var(--t-bright)', fontFamily: 'inherit',
            }}>
              STERLING
            </span>
          </button>
          {kiteOnly && <><span className="workspace-current">KITE</span><span id="kite-session-status" /></>}
          {!kiteOnly && (spectrum === 'kite' ? <SpectrumNavigation workspace={spectrum} onSelect={chooseSpectrum} /> : <div className="sterling-media-destinations"><SpectrumNavigation workspace={spectrum} onSelect={chooseSpectrum} /></div>)}

          <div style={{ flex: 1 }} />
          {kiteOnly && <div id="sterling-mobile-header-actions" className="sterling-mobile-header-actions" />}
          {spectrum === 'kite' && <div className="kite-header-actions" style={{ display: 'flex', alignItems: 'center', gap: 6, height: '100%', minWidth: 0 }}>
            <div className="kite-topnav">
              {primary.map((item) => (
                <button
                  key={item.id}
                  onClick={() => handleKiteNav(item.id)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    fontFamily: 'inherit', fontSize: 13, fontWeight: kiteNav === item.id ? 500 : 400,
                    color: kiteNav === item.id ? 'var(--k-brand)' : 'var(--k-text)',
                    padding: '0 9px', height: '100%',
                    transition: 'color .15s ease',
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {overflow.length > 0 && <div className="sterling-desktop-tools" ref={toolsRef} style={{ position: 'relative', flexShrink: 0 }}>
              <button type="button" aria-haspopup="menu" aria-expanded={toolsOpen}
                onClick={() => setToolsOpen(open => !open)}
                style={{ height: 30, border: '1px solid var(--t-border)', background: 'transparent',
                  color: overflow.some(item => item.id === kiteNav) ? 'var(--k-brand)' : 'var(--t-text)',
                  padding: '0 10px', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                Tools ▾
              </button>
              {toolsOpen && <div role="menu" aria-label="Additional sections"
                style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', width: 180,
                  maxHeight: 'min(65vh, 420px)', overflowY: 'auto', padding: 5,
                  border: '1px solid var(--t-border)', borderRadius: 8,
                  background: 'var(--k-bg)', boxShadow: '0 8px 28px rgba(0,0,0,.22)', zIndex: 100 }}>
                {overflow.map(item => <button key={item.id} type="button" role="menuitem"
                  aria-current={kiteNav === item.id ? 'page' : undefined}
                  onClick={() => handleKiteNav(item.id)}
                  style={{ display: 'block', width: '100%', padding: '9px 12px',
                    textAlign: 'left', border: 0, borderRadius: 5, cursor: 'pointer',
                    background: kiteNav === item.id ? 'var(--k-surface-2)' : 'transparent',
                    color: kiteNav === item.id ? 'var(--k-brand)' : 'var(--k-text)' }}>
                  {item.label}
                </button>)}
              </div>}
            </div>}
            <span style={{ width: 1, height: 18, background: 'var(--t-border)', margin: '0 8px', flexShrink: 0 }} />




            {/* More options (three dots) */}
            <button onClick={() => setShowSettings(true)} title="More options" style={{
              background: 'none', border: '1px solid var(--t-border)', cursor: 'pointer',
              width: 32, height: 32, borderRadius: 8,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--t-dim)', fontSize: 16, lineHeight: 1, transition: 'border-color .12s, color .12s',
            }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-bright)44'; (e.currentTarget as HTMLElement).style.color = 'var(--t-bright)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-border)'; (e.currentTarget as HTMLElement).style.color = 'var(--t-dim)'; }}
            >⋮</button>
          </div>}

          {spectrum==='tv' && <MediaHeaderControls
            kind="tv" theme={tvState.theme} query={tvState.query}
            onTheme={theme=>{setTvState(s=>({...s,theme}));tvCommand('theme',theme);}}
            onQuery={query=>{setTvState(s=>({...s,query}));tvCommand('search',query);}}
            onEnter={()=>tvCommand('play-search')} onFeatures={()=>tvCommand('more')}
          />}
          {spectrum === 'radio' && <MediaHeaderControls
            kind="radio" theme={radioState.theme} query={radioState.query}
            onTheme={theme=>{setRadioState(s=>({...s,theme}));radioCommand('theme',theme);}}
            onQuery={query=>{setRadioState(s=>({...s,query}));radioCommand('search',query);}}
            onFeatures={()=>radioCommand('more')}
          />}
          {(spectrum === 'tv' || spectrum === 'radio') && !navOptions.spectrumVisible.kite && <button type="button" aria-label="Experience settings"
            title="Experience settings" onClick={() => setShowSettings(true)}
            style={{ background:'none', border:'1px solid var(--t-border)', cursor:'pointer',
              width:32, height:32, borderRadius:8, color:'var(--t-dim)', marginLeft:8 }}>
            ⚙
          </button>}
          {!kiteOnly && !isConfiguredHome && (
            <button
              type="button"
              className="sterling-mobile-close-page"
              aria-label="Close page and return to configured home"
              title="Return Home"
              onClick={goHome}
            >
              <span aria-hidden="true">×</span>
            </button>
          )}
        </div>
        {!kiteOnly && spectrum === 'kite' && (
          <div className="sterling-mobile-command-row" aria-label="Mobile navigation and search">
            <span className="sterling-mobile-current-section">{mobilePaneTitle || (navigation.find(item => item.id === kiteNav)?.label ?? kiteNav)}</span>
            <div id="sterling-mobile-header-actions" className="sterling-mobile-header-actions" />
          </div>
        )}
      </div>

      {/* The Simulation banner (virtual money, no real orders), or the chip that points to it. */}
      {spectrum === 'kite' && <MobileExperienceMenu userName={sterlingProfile?.display_name || sterlingProfile?.username || undefined}
        activeNav={kiteNav} onNavClick={handleKiteNav} tools={overflow}
        onSearch={() => {
          if (chooseSpectrum('kite','live')) {
            requestAnimationFrame(() => window.dispatchEvent(new CustomEvent('kite-open-watchlist-search')));
          }
        }} />}
      {spectrum === 'kite' && <SimBanner />}
      {spectrum === 'kite' && selectedView === 'live' && <KiteSessionBar />}

      {/* ── Content ──────────────────────────────────────────────── */}
      {spectrum === 'kite' && <div id="sterling-kite-surface" role="tabpanel" aria-label="KITE workspace"
        hidden={spectrum !== 'kite'}
        style={{ display: spectrum === 'kite' ? 'flex' : 'none', flex: 1,
          minHeight: 0, overflow: 'hidden', flexDirection: 'column' }}>
        <KiteTab />
      </div>}
      {spectrum === 'radio' && <div id="sterling-radio-surface" role="tabpanel"
        aria-label="Radio workspace" style={{ display:'flex', flex:1, minHeight:0, minWidth:0, overflow:'hidden' }}>
        <RadioWorkspace onState={onRadioState} onPlayerState={setRadioPlayerExpanded} />
      </div>}
      {spectrum === 'tv' && <div id="sterling-tv-surface" role="tabpanel"
        aria-label="TV workspace" style={{ display: 'flex', flex: 1,
          minHeight: 0, minWidth: 0, overflow: 'hidden' }}>
        <TvWorkspace onState={onTvState} onPlayerState={setTvPlayerExpanded} />
      </div>}

      <SimpleSettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />
    </div>
  );
}

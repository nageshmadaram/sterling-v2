import { useViewStore } from '../store/useViewStore';
import { ProfileMenu, useSterlingProfile } from '../components/kite/ProfileMenu';
import { useExperienceOptions, orderedNavigation } from '../store/useExperienceOptions';
import React, { useState, useEffect, useRef } from 'react';
import { SimpleSettingsDrawer } from '../components/SimpleSettings';
import { KiteTab } from '../components/kite/KiteTab';
import { ThemeToggle } from '../components/kite/ThemeToggle';
import type { NavItem } from '../components/kite/KiteLayout';
import { useKiteSettings } from '../store/useKiteSettings';
import { hasUnsavedDraft } from '../components/kite/config/unsavedDraftGuard';
import { SterlingLogo } from '../components/SterlingLogo';
import { ViewSwitch } from '../components/sim/ViewSwitch';
import { SimBanner } from '../components/sim/SimBanner';
import '../styles/terminal.css';
import { MobileExperienceMenu } from '../components/kite/MobileExperienceMenu';
import { useMobileExperience } from '../store/useMobileExperience';

function notificationDestination(value:string|null):NavItem|null{return value==='pcr'?'pcr':value==='alerts'?'positions':value==='signals'?'dashboard':null;}

export function SimpleTerminal() {
  const mobileStyle = useMobileExperience(s => s.style);
  useEffect(() => { document.body.dataset.mobileStyle = mobileStyle; return () => { delete document.body.dataset.mobileStyle; }; }, [mobileStyle]);
  useEffect(()=>{const params=new URLSearchParams(window.location.search);if(params.get('view')==='live'&&notificationDestination(params.get('notification')))useViewStore.getState().setView('live');},[]);
  const [showSettings, setShowSettings] = useState(false);
  const preferredSection = useKiteSettings((s) => s.defaultSection);
  const userNavigatedRef = useRef(!!notificationDestination(new URLSearchParams(window.location.search).get('notification')));
  const [kiteNav, setKiteNav] = useState<NavItem>(() => notificationDestination(new URLSearchParams(window.location.search).get('notification')) || useKiteSettings.getState().defaultSection || 'dashboard');
  const navOptions=useExperienceOptions(s=>s.options);
  const {data:sterlingProfile}=useSterlingProfile();

  useEffect(() => {
    if (!userNavigatedRef.current && preferredSection && preferredSection !== kiteNav) {
      setKiteNav(preferredSection);
    }
  }, [preferredSection, kiteNav]);

  const handleKiteNav = (nav: NavItem) => {
    userNavigatedRef.current = true;
    if (nav !== kiteNav && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('kite-scroll-to-draft-bar'));
      if (!window.confirm('You have unsaved settings changes. Leave this page and discard them?')) {
        return;
      }
    }
    setKiteNav(nav);
    window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: nav }));
  };

  useEffect(() => {
    const onNav = (event: Event) => {
      const next = (event as CustomEvent<NavItem>).detail;
      if (typeof next === 'string') {
        userNavigatedRef.current = true;
        setKiteNav(next);
      }
    };
    window.addEventListener('kite-nav-click', onNav);
    window.addEventListener('kite-page-changed', onNav);
    return () => { window.removeEventListener('kite-nav-click', onNav); window.removeEventListener('kite-page-changed', onNav); };
  }, []);

  useEffect(()=>{if(!('serviceWorker' in navigator))return;const receive=(event:MessageEvent)=>{if(event.data?.type!=='sterling-notification')return;try{const url=new URL(event.data.url,window.location.origin);if(url.origin!==window.location.origin)return;const next=notificationDestination(url.searchParams.get('notification'));if(next){if(hasUnsavedDraft()&&!window.confirm('Open this live-account notification and discard unapplied settings?'))return;useViewStore.getState().setView('live');window.location.assign(url.href);}}catch{/* malformed notification */}};navigator.serviceWorker.addEventListener('message',receive);return()=>navigator.serviceWorker.removeEventListener('message',receive);},[kiteNav]);

  return (
    <div className="term-root">
      <style>{`
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
        borderBottom: '1px solid var(--t-border)',
      }}>
        {/* Row 1: STERLING | KITE | [kite nav] | [actions] */}
        <div className="sterling-header-row" style={{
          display: 'flex', alignItems: 'center', gap: 0,
          height: 44, padding: '0 20px',
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9, marginRight: 16, userSelect: 'none' }}>
            <SterlingLogo size={24} />
            <span style={{
              fontSize: 17, fontWeight: 800, letterSpacing: '0.18em',
              color: 'var(--t-bright)', fontFamily: 'inherit',
            }}>
              STERLING
            </span>
          </span>
          <ViewSwitch />

          <div style={{ flex: 1 }} />
          <div className="kite-header-actions" style={{ display: 'flex', alignItems: 'center', gap: 6, height: '100%', minWidth: 0 }}>
            <div className="kite-topnav">
              {orderedNavigation([
                { id: 'dashboard' as const, label: 'Dashboard' },
                { id: 'astro' as const, label: 'Astrology' },
                { id: 'pcr' as const, label: 'PCR' },
                { id: 'orders' as const, label: 'Orders' },
                { id: 'holdings' as const, label: 'Holdings' },
                { id: 'positions' as const, label: 'Positions' },
                { id: 'journal' as const, label: 'Journal' },
                { id: 'more' as const, label: 'More' },
                { id: 'data' as const, label: 'Data' },
                { id: 'backtest' as const, label: 'Backtest' },
                { id: 'connect' as const, label: 'Connect' },
                { id: 'help' as const, label: 'Help' },
              ], navOptions).map((item) => (
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
            <span style={{ width: 1, height: 18, background: 'var(--t-border)', margin: '0 8px', flexShrink: 0 }} />

            <ThemeToggle />
            <button title="Notifications" style={{
              background: 'none', border: '1px solid var(--t-border)', cursor: 'pointer',
              width: 32, height: 32, borderRadius: 8,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--t-dim)', fontSize: 13, transition: 'border-color .12s, color .12s',
            }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-bright)44'; (e.currentTarget as HTMLElement).style.color = 'var(--t-bright)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-border)'; (e.currentTarget as HTMLElement).style.color = 'var(--t-dim)'; }}
            >🔔</button>

            <ProfileMenu />

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
          </div>
        </div>
      </div>

      {/* The Simulation banner (virtual money, no real orders), or the chip that points to it. */}
      <MobileExperienceMenu userName={sterlingProfile?.display_name || sterlingProfile?.username || undefined} activeNav={kiteNav} onNavClick={handleKiteNav} />
      <SimBanner />

      {/* ── Content ──────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <KiteTab />
      </div>

      <SimpleSettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />
    </div>
  );
}

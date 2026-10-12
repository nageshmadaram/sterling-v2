import { createPortal } from 'react-dom';
import React, { useState, useEffect, useRef } from 'react';
import { THEME_PRESETS, FONTS, type ThemePreset } from '../../appearance/catalog';
import { useAppearance, DEFAULT_APPEARANCE, validateAppearance } from '../../appearance/store';
import { useStore } from '../../store/useStore';

export function AppearanceSettings() {
  const { settings, update, replace } = useAppearance();
  const base = useStore(s => s.theme);
  const setTheme = useStore(s => s.setTheme);
  const [search, setSearch] = useState('');
  const [family, setFamily] = useState('All');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [transfer, setTransfer] = useState('');
  const [message, setMessage] = useState('');
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const [preview, setPreview] = useState<ThemePreset | null>(null);
  useEffect(() => { if (preview) return () => previewTrigger.current?.focus(); }, [preview]);
  const current = THEME_PRESETS.find(p => p.id === settings.preset && p.dark === (base !== 'light'));
  const choose = (p: ThemePreset) => { setTheme(p.dark ? 'dark' : 'light'); update({ preset: p.id, base: p.dark ? 'dark' : 'light', background: p.family === 'Nothing' ? 'dots' : p.family === 'Apple' ? 'glow' : 'plain' }); setPreview(null); setMessage(`${p.name} applied.`); };
  const shown = THEME_PRESETS.filter(p => (family === 'All' || p.family === family) && (!favoritesOnly || settings.favorites.includes(p.id)) && `${p.name} ${p.family}`.toLowerCase().includes(search.toLowerCase()));
  return <section className="appearance-settings" aria-label="Themes and appearance">
    <header className="appearance-heading"><div><div className="appearance-eyebrow">EXPERIENCE / APPEARANCE</div><h2>Themes & appearance</h2><p>58 themes. Local fonts. A look that stays yours across reloads.</p></div><span className="appearance-current">{current?.name ?? `Classic ${base}`} · selected</span></header>
    <div className="appearance-toolbar">
      <input aria-label="Search themes" placeholder="Find Apple, Ubuntu, Terminal…" value={search} onChange={e => setSearch(e.target.value)} />
      <select aria-label="Theme family" value={family} onChange={e => setFamily(e.target.value)}>{['All', ...new Set(THEME_PRESETS.map(p => p.family))].map(f => <option key={f}>{f}</option>)}</select>
      <button aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)}>★ Favourites</button>
    </div>
    <div className="appearance-classics"><span>Classic</span>{(['light','dark','grey'] as const).map(t => <button key={t} aria-pressed={!current && base === t} onClick={() => { update({ preset: '', accent: '', base:t }); setTheme(t); }}>{t}</button>)}</div>
    <div className="appearance-grid" aria-label="Theme gallery">{shown.map(p => <article key={p.id} className="appearance-tile" style={{ background: p.bg, color: p.text, borderColor: current?.id === p.id ? p.accent : 'transparent', borderRadius: p.radius || 3 }}>
      <button className="appearance-preview" aria-label={`Preview ${p.name}`} onClick={e => { previewTrigger.current=e.currentTarget; setPreview(p); }} style={{ color:p.text, fontFamily:FONTS[p.font] }}>
        <span className="appearance-mini" style={{ background:p.surface }}><span style={{ background:p.accent }} /><span /><span /><i style={{ color:p.accent }}>Aa 123</i></span>
        <strong>{p.name}</strong><small>{p.family} · {p.dark ? 'Dark' : 'Light'}</small>
      </button>
      <div className="appearance-tile-actions"><button aria-label={`Apply ${p.name}`} onClick={() => choose(p)} style={{ color:p.accent }}>{current?.id === p.id ? '✓ Selected' : 'Apply'}</button><button aria-label={`Favourite ${p.name}`} aria-pressed={settings.favorites.includes(p.id)} onClick={() => update({ favorites: settings.favorites.includes(p.id) ? settings.favorites.filter(id => id !== p.id) : [...settings.favorites,p.id] })}>{settings.favorites.includes(p.id) ? '★' : '☆'}</button></div>
    </article>)}</div>
    {!shown.length && <p role="status">No themes match. Try another family or search.</p>}
    <div className="appearance-options">
      <label>Typeface<select aria-label="Appearance font" value={settings.font} onChange={e => update({ font:e.target.value as typeof settings.font })}>{['preset',...Object.keys(FONTS)].map(f => <option key={f}>{f}</option>)}</select><small>Uses installed fonts with local fallbacks.</small></label>
      <label>Motion<select aria-label="Appearance motion" value={settings.motion} onChange={e => update({ motion:e.target.value as typeof settings.motion })}>{['system','off','calm','expressive'].map(v => <option key={v}>{v}</option>)}</select><small>System reduced-motion preference always wins.</small></label>
      <label>Backdrop<select aria-label="Appearance background" value={settings.background} onChange={e => update({ background:e.target.value as typeof settings.background })}>{['plain','glow','grid','dots'].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Corners<select aria-label="Appearance corners" value={settings.corners} onChange={e => update({ corners:e.target.value as typeof settings.corners })}>{['preset','square','soft'].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Accent<input aria-label="Appearance accent" type="color" value={settings.accent || current?.accent || '#2563eb'} onChange={e => update({ accent:e.target.value })} /><button onClick={() => update({ accent:'' })}>Use theme accent</button></label>
    </div>
    <details className="appearance-transfer"><summary>Save, share & restore appearance</summary><p>Export only visual preferences. Paste a saved package to import it.</p><textarea aria-label="Appearance package" value={transfer} onChange={e => setTransfer(e.target.value)} placeholder="Appearance JSON" />
      <div className="appearance-toolbar"><button onClick={() => { setTransfer(JSON.stringify({ ...settings, base },null,2)); setMessage('Appearance package ready to copy.'); }}>Export package</button><button onClick={() => { try { const next = validateAppearance(JSON.parse(transfer)); const p = THEME_PRESETS.find(x => x.id === next.preset); setTheme(p ? p.dark ? 'dark' : 'light' : next.base); replace(next); setMessage('Appearance imported.'); } catch (e) { setMessage(e instanceof Error ? e.message : 'Invalid appearance package.'); } }}>Import package</button><button onClick={() => { replace(DEFAULT_APPEARANCE); setTheme('light'); setMessage('Classic light appearance restored.'); }}>Restore defaults</button></div>
    </details>
    <p role="status" aria-live="polite" className="appearance-message">{message || 'Changes save automatically to your account. Buy/sell colours keep their trading meaning.'}</p>
    {preview && createPortal(<div className="appearance-modal" role="dialog" aria-modal="true" aria-label={`${preview.name} preview`} onKeyDown={e => { if (e.key === 'Escape') setPreview(null); if (e.key === 'Tab') { const buttons = e.currentTarget.querySelectorAll<HTMLButtonElement>('button'); const first=buttons[0],last=buttons[buttons.length-1]; if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();} else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();} } }}><div style={{ background:preview.bg,color:preview.text,borderRadius:preview.radius || 3,fontFamily:FONTS[preview.font] }}><button autoFocus aria-label="Close theme preview" onClick={() => setPreview(null)}>×</button><small>{preview.family} / {preview.dark ? 'DARK' : 'LIGHT'}</small><h2>{preview.name}</h2><p>A calmer place to make your next decision.</p><div className="appearance-sample" style={{ background:preview.surface }}><strong>NIFTY · Watchlist</strong><span style={{ color:preview.accent }}>22,520.45</span><p>Workspace preview · sample values</p><svg viewBox="0 0 300 70" aria-label="Sample chart"><path d="M0 60 L30 48 L60 55 L90 25 L120 39 L150 20 L180 35 L210 18 L240 28 L270 9 L300 17" fill="none" stroke={preview.accent} strokeWidth="3" /></svg></div><div className="appearance-toolbar"><button onClick={() => setPreview(null)}>Cancel</button><button onClick={() => choose(preview)}>Apply {preview.name}</button></div></div></div>,document.body)}
  </section>;
}

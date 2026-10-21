import { useEffect, useRef, useState, useId } from 'react';
import { readMediaSuggestions, type MediaSuggestion } from './mediaSuggestions';

export type MediaTheme = 'spotify' | 'youtube' | 'netflix';

type Props = {
  kind: 'tv' | 'radio';
  theme: MediaTheme;
  query: string;
  onTheme: (theme: MediaTheme) => void;
  onQuery: (query: string) => void;
  onEnter?: () => void;
  onFeatures?: () => void;
};

/** Same compact, touch-accessible controls for video and audio workspaces. */
export function MediaHeaderControls({ kind, theme, query, onTheme, onQuery, onEnter, onFeatures }: Props) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [suggestions,setSuggestions]=useState<MediaSuggestion[]>([]);
  const [activeSuggestion,setActiveSuggestion]=useState(-1);
  const suggestionId=useId();
  const queryRef=useRef(query);queryRef.current=query;
  const selectSuggestion=(item:MediaSuggestion)=>{window.dispatchEvent(new CustomEvent(`sterling-${kind}-command`,{detail:{action:'select-suggestion',value:item.id}}));setSearchOpen(false);setSuggestions([]);};
  useEffect(()=>{
    const receive=(event:Event)=>{const detail=(event as CustomEvent).detail;if(detail?.query!==queryRef.current)return;setSuggestions(readMediaSuggestions(detail.items));setActiveSuggestion(-1);};
    window.addEventListener(`sterling-${kind}-suggestions`,receive);
    return()=>window.removeEventListener(`sterling-${kind}-suggestions`,receive);
  },[kind]);
  useEffect(()=>{if(!searchOpen)return;setSuggestions([]);setActiveSuggestion(-1);window.dispatchEvent(new CustomEvent(`sterling-${kind}-command`,{detail:{action:'suggest',value:query}}));},[query,searchOpen,kind]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const previousQuery = useRef(query);

  useEffect(() => {
    // A selected channel or station clears its query via the iframe bridge.
    // Collapse the bar when that happens; reopening is one search-icon tap.
    if(previousQuery.current.trim() && !query.trim())setSearchOpen(false);
    previousQuery.current=query;
  },[query]);

  useEffect(() => {
    const focusFromPlayer = () => {
      setSettingsOpen(false);
      setSearchOpen(true);
      requestAnimationFrame(() => searchInput.current?.focus());
    };
    const event = kind === 'tv' ? 'sterling-tv-focus-search' : 'sterling-radio-focus-search';
    window.addEventListener(event, focusFromPlayer);
    return () => window.removeEventListener(event, focusFromPlayer);
  }, [kind]);

  useEffect(() => {
    if (!settingsOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setSettingsOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); setSettingsOpen(false); }
    };
    document.addEventListener('pointerdown', dismiss, true);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', escape, true);
    };
  }, [settingsOpen]);

  const toggleSearch = () => {
    setSettingsOpen(false);
    if (searchOpen) {
      // A hidden query must not keep search filters/category rows suppressed.
      if (query) onQuery('');
      setSearchOpen(false);
      return;
    }
    setSearchOpen(true);
    requestAnimationFrame(() => searchInput.current?.focus());
  };
  const themes: { id: MediaTheme; label: string; note: string }[] = [
    { id: 'spotify', label: 'Spotify', note: 'Library and music-style player' },
    { id: 'youtube', label: kind === 'radio' ? 'YouTube Music' : 'YouTube', note: 'Video-style feed and discovery' },
    { id: 'netflix', label: 'Netflix', note: 'Cinematic collections' },
  ];
  return (
    <div ref={root} className="sterling-media-quick-actions" data-media-kind={kind}>
      <button type="button" className="sterling-media-icon-button" aria-label={searchOpen ? 'Close search' : `Search ${kind === 'tv' ? 'TV channels' : 'radio stations'}`} aria-expanded={searchOpen}
        onClick={toggleSearch}>
        {searchOpen ? <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
          : <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>}
      </button>
      <button type="button" className="sterling-media-icon-button" aria-label={kind === 'tv' ? 'TV display settings' : 'Radio display settings'} aria-haspopup="menu" aria-expanded={settingsOpen}
        onClick={() => { if (searchOpen && query) onQuery(''); setSearchOpen(false); setSettingsOpen(o => !o); }}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2.75v2.1m0 14.3v2.1M4.4 4.4l1.5 1.5m12.2 12.2 1.5 1.5M2.75 12h2.1m14.3 0h2.1M4.4 19.6l1.5-1.5M18.1 5.9l1.5-1.5"/><circle cx="12" cy="12" r="4"/></svg>
      </button>
      {settingsOpen && <div className="sterling-media-settings-popover" role="menu" aria-label={kind === 'tv' ? 'TV appearance' : 'Radio appearance'}>
        <p className="sterling-media-settings-label">Appearance</p>
        {themes.map(option => (
          <button type="button" key={option.id} role="menuitemradio" aria-checked={theme === option.id}
            onClick={() => { onTheme(option.id); setSettingsOpen(false); }}>
            <span><strong>{option.label}</strong><small>{option.note}</small></span>
            <span aria-hidden="true">{theme === option.id ? '✓' : ''}</span>
          </button>
        ))}
        <div className="sterling-media-popover-divider" />
        <button type="button" role="menuitem" onClick={() => {
          setSettingsOpen(false);
          window.dispatchEvent(new CustomEvent(`sterling-${kind}-command`,{detail:{action:'startup'}}));
        }}>Default {kind === 'tv' ? 'channel' : 'station'} <span aria-hidden="true">›</span></button>
        {onFeatures && <><div className="sterling-media-popover-divider" /><button type="button" role="menuitem" onClick={() => { setSettingsOpen(false); onFeatures(); }}>Player tools and preferences <span aria-hidden="true">›</span></button></>}
      </div>}
      {searchOpen && <form className="sterling-media-expanded-search" role="search" onSubmit={event => { event.preventDefault(); if(activeSuggestion>=0&&suggestions[activeSuggestion])selectSuggestion(suggestions[activeSuggestion]);else onEnter?.(); }}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        <input role="combobox" aria-autocomplete="list" aria-expanded={suggestions.length>0} aria-controls={suggestionId} aria-activedescendant={activeSuggestion>=0?`${suggestionId}-${activeSuggestion}`:undefined} onKeyDown={event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();setActiveSuggestion(index=>suggestions.length?(index<0?(event.key==='ArrowDown'?0:suggestions.length-1):(index+(event.key==='ArrowDown'?1:-1)+suggestions.length)%suggestions.length):-1);}if(event.key==='Escape'){setSearchOpen(false);onQuery('');}}} ref={searchInput} type="search" maxLength={200} value={query} onChange={event => onQuery(event.currentTarget.value)}
          aria-label={kind === 'tv' ? 'Search TV channels' : 'Search radio stations'}
          placeholder={kind === 'tv' ? 'What do you want to watch?' : 'What do you want to listen to?'} />
        {query && <button type="button" aria-label="Clear search" onClick={() => { onQuery(''); searchInput.current?.focus(); }}>×</button>}
        <div className="sterling-media-suggestions"><p>{query.trim()?'Suggestions':'Recently played & picks'}</p><div role="listbox" id={suggestionId} aria-label="Search suggestions">{suggestions.map((item,index)=><button type="button" role="option" aria-selected={activeSuggestion===index} id={`${suggestionId}-${index}`} key={item.id} onMouseDown={event=>event.preventDefault()} onClick={()=>selectSuggestion(item)}>{item.artwork?<img src={item.artwork} alt="" onError={event=>{event.currentTarget.style.visibility='hidden';}}/>:<span className="sterling-suggestion-art" aria-hidden="true">{item.label.slice(0,1)}</span>}<span><strong>{item.label}</strong><small>{item.subtitle}</small></span><span aria-hidden="true">↗</span></button>)}</div>{!suggestions.length&&<small role="status">{query?'No matches yet. Try a name, language or category.':'Choose a station or channel to get started.'}</small>}</div>
        {kind === 'tv' && <button type="submit" aria-label="Play first matching TV channel">Play</button>}
      </form>}
    </div>
  );
}

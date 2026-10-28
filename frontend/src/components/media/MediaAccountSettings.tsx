import type { MediaTheme } from './MediaHeaderControls';

export function MediaAccountSettings({kind,theme,onTheme,onFeatures}:{
  kind:'tv'|'radio'; theme:MediaTheme; onTheme:(value:MediaTheme)=>void; onFeatures?:()=>void;
}) {
  // The account menu is a native top-layer dialog. Close it before opening
  // a player dialog, otherwise the new sheet appears behind the account menu.
  const launch=(run:()=>void)=>{
    window.dispatchEvent(new Event('sterling-close-account-menu'));
    requestAnimationFrame(run);
  };
  const command=(action:string)=>launch(()=>window.dispatchEvent(new CustomEvent(
    `sterling-${kind}-command`,{detail:{action}})));
  return <div className="media-account-settings" aria-label={kind==='tv'?'Space player appearance':'Echo player appearance'}>
    <strong className="media-account-heading">☀ {kind==='tv'?'Space':'Echo'} appearance & player</strong>
    <div className="media-account-appearance" role="group" aria-label="Player interface">
      {(['spotify','youtube','netflix'] as const).map(mode=><button key={mode}
        type="button" aria-pressed={theme===mode} onClick={()=>launch(()=>onTheme(mode))}>
        {mode==='youtube'?(kind==='radio'?'YouTube Music':'YouTube'):mode==='netflix'?'Netflix':'Spotify'}
      </button>)}
    </div>
    <div className="media-account-actions">
      <button type="button" onClick={()=>command('startup')}>Default {kind==='tv'?'channel':'station'}</button>
      <button type="button" onClick={()=>command('sources')}>{kind==='tv'?'Channel':'Station'} sources</button>
      {onFeatures&&<button type="button" onClick={()=>launch(onFeatures)}>Player tools & preferences</button>}
    </div>
  </div>;
}

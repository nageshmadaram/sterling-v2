import { createPortal } from 'react-dom';
import { useCallback, useState, useLayoutEffect } from 'react';
import { TvWorkspace } from '../components/tv/TvWorkspace';
import { RadioWorkspace } from '../components/radio/RadioWorkspace';
import { MediaHeaderControls, type MediaTheme } from '../components/media/MediaHeaderControls';
export default function MediaWorkspace({ feature }: { feature: 'tv' | 'radio' }) {
  const [state, setState] = useState<{ theme: MediaTheme; query: string }>({ theme: feature === 'tv' ? 'youtube' : 'spotify', query: '' });
  const onState = useCallback((value: { theme: MediaTheme; query: string }) => setState(value), []);
  const [toolbar,setToolbar]=useState<HTMLElement|null>(null);
  useLayoutEffect(()=>setToolbar(document.getElementById('workspace-toolbar')),[]);
  const command = (action: string, value?: string) => window.dispatchEvent(new CustomEvent(`sterling-${feature}-command`, { detail: { action, value } }));
  return <main className="workspace-media" aria-label={feature === 'tv' ? 'Space workspace' : 'Echo workspace'}>
    {toolbar && createPortal(
      <MediaHeaderControls kind={feature} theme={state.theme} query={state.query} onTheme={value => command('theme', value)} onQuery={value => command('search', value)} onFeatures={() => command('more')} />,
      toolbar)}
    {feature === 'tv' ? <TvWorkspace onState={onState} /> : <RadioWorkspace onState={onState} />}
  </main>;
}

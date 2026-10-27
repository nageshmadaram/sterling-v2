import { createPortal } from 'react-dom';
import { useCallback, useState } from 'react';
import { TvWorkspace } from '../components/tv/TvWorkspace';
import { RadioWorkspace } from '../components/radio/RadioWorkspace';
import { MediaHeaderControls, type MediaTheme } from '../components/media/MediaHeaderControls';
export default function MediaWorkspace({ feature }: { feature: 'tv' | 'radio' }) {
  const [state, setState] = useState<{ theme: MediaTheme; query: string }>({ theme: feature === 'tv' ? 'youtube' : 'spotify', query: '' });
  const onState = useCallback((value: { theme: MediaTheme; query: string }) => setState(value), []);
  const command = (action: string, value?: string) => window.dispatchEvent(new CustomEvent(`sterling-${feature}-command`, { detail: { action, value } }));
  return <main className="workspace-media" aria-label={feature === 'tv' ? 'Space workspace' : 'Echo workspace'}>
    {document.getElementById('workspace-toolbar') && createPortal(
      <MediaHeaderControls kind={feature} theme={state.theme} query={state.query} onTheme={value => command('theme', value)} onQuery={value => command('search', value)} onFeatures={() => command('more')} />,
      document.getElementById('workspace-toolbar')!)}
    {feature === 'tv' ? <TvWorkspace onState={onState} /> : <RadioWorkspace onState={onState} />}
  </main>;
}

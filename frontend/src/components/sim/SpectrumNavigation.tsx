import { useView, type AppView } from '../../store/useViewStore';
import {
  useExperienceOptions, type SpectrumProduct,
} from '../../store/useExperienceOptions';
import { useSimStatus, isOwnerDenied } from '../../hooks/useSim';
import { ViewSwitch } from './ViewSwitch';

export interface SpectrumNavigationProps {
  workspace: 'kite'|'tv';
  onSelect: (workspace: 'kite'|'tv', view?: AppView) => void;
}

/**
 * Uses the ORIGINAL KITE switch class and location, not a replacement pill
 * control. TV and optional standalone tabs inherit the same appearance.
 * Simulation access stays subject to the same owner-status proof as ViewSwitch.
 */
export function SpectrumNavigation({ workspace, onSelect }: SpectrumNavigationProps) {
  const options = useExperienceOptions(s => s.options);
  const view = useView();
  const status = useSimStatus();
  const simulationAllowed = view === 'simulation' || status.isSuccess ||
    (status.isError && !isOwnerDenied(status.error));
  const { spectrumVisible: visible, spectrumLayout: layout } = options;
  const navButton = (key: SpectrumProduct, label: string, selected: boolean,
                     onClick: () => void) =>
    <button key={key} type="button"
      className="mobile-view-toggle unified-view-toggle"
      data-testid={`spectrum-${key}-tab`}
      aria-current={selected ? 'page' : undefined}
      aria-label={label}
      title={label}
      onClick={onClick}>{label}</button>;

  const destinations = options.spectrumOrder.flatMap(key => {
    if (!visible[key]) return [];
    if (key === 'tv') return [navButton('tv',options.tvLabel,workspace==='tv',
      () => onSelect('tv'))];
    if (key === 'kite') {
      if (layout === 'toggle') {
        // Preserve the pre-TV KITE/KITE SIMULATION switch itself. If the
        // operator hides Simulation, do not allow a new switch into it; if
        // currently in Simulation, still retain the return route.
        return [(visible.simulation || view === 'simulation') && simulationAllowed
          ? <ViewSwitch key="kite" />
          : navButton('kite','KITE',workspace==='kite',
            () => onSelect('kite','live'))];
      }
      return [navButton('kite','KITE',workspace==='kite' && view==='live',
        () => onSelect('kite','live'))];
    }
    if (key === 'simulation' && layout === 'separate' && simulationAllowed) {
      return [navButton('simulation','Simulation',
        workspace==='kite' && view==='simulation',
        () => onSelect('kite','simulation'))];
    }
    if (key === 'simulation' && layout === 'toggle' && !visible.kite
        && simulationAllowed) {
      return [navButton('simulation','Simulation',
        workspace==='kite' && view==='simulation',
        () => onSelect('kite','simulation'))];
    }
    return [];
  });
  // If a saved visibility configuration hides KITE while the app is already
  // in Simulation, always provide a way back to live trading view.
  if (!visible.kite && view === 'simulation') {
    destinations.unshift(navButton('kite','KITE',workspace==='kite' && view==='live',
      () => onSelect('kite','live')));
  }
  return <nav aria-label="Sterling Spectrum" style={{
    display:'inline-flex', alignItems:'center', gap:4, minWidth:0,
  }}>{destinations}</nav>;
}

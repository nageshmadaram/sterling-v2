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
  const cycleChoices = options.spectrumOrder.filter(key =>
    visible[key] && (key !== 'simulation' || simulationAllowed));
  const currentProduct: SpectrumProduct = workspace === 'tv' ? 'tv'
    : view === 'simulation' ? 'simulation' : 'kite';
  const activeCycleProduct = cycleChoices.includes(currentProduct)
    ? currentProduct : cycleChoices[0] || 'kite';
  const cycleNext = () => {
    if (cycleChoices.length < 2) return;
    const next = cycleChoices[
      (cycleChoices.indexOf(activeCycleProduct) + 1) % cycleChoices.length
    ];
    if (next === 'tv') onSelect('tv');
    else onSelect('kite',next === 'simulation' ? 'simulation' : 'live');
  };
  if (layout === 'cycle') {
    const displayed = activeCycleProduct === 'tv' ? options.tvLabel
      : activeCycleProduct === 'simulation' ? 'Simulation' : 'KITE';
    return <button type="button" role="button" data-testid="spectrum-cycle-switch"
      data-active-view={activeCycleProduct === 'simulation' ? 'simulation' : 'live'}
      className="mobile-view-toggle unified-view-toggle"
      aria-label={`Cycle Spectrum: ${displayed}`}
      title={cycleChoices.length > 1 ? 'Switch to next Spectrum workspace' : displayed}
      onClick={cycleNext}>{displayed}</button>;
  }
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
        return [workspace === 'kite' &&
          (visible.simulation || view === 'simulation') && simulationAllowed
          ? <ViewSwitch key="kite" />
          : navButton('kite','KITE',workspace==='kite',
            () => onSelect('kite', workspace === 'tv' ? undefined : 'live'))];
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
    // In this branch the view is necessarily 'simulation', so the
    // fallback KITE/live destination is not selected yet. Keep it visible
    // as an emergency route out of a hidden Simulation workspace.
    destinations.unshift(navButton('kite','KITE',false,
      () => onSelect('kite','live')));
  }
  // Keep each button an IMMEDIATE child of .sterling-header-row.
  // Sterling's original desktop and mobile CSS uses the direct-child
  // selector for the KITE switch. A <nav> wrapper changes its styling.
  return <>{destinations}</>;
}

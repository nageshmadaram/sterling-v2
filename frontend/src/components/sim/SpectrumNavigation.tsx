import { useView, type AppView } from '../../store/useViewStore';
import { useExperienceOptions, type SpectrumProduct } from '../../store/useExperienceOptions';
import { useSimStatus, isOwnerDenied } from '../../hooks/useSim';

export interface SpectrumNavigationProps {
  workspace: 'kite'|'tv'|'radio';
  onSelect: (workspace: 'kite'|'tv'|'radio', view?: AppView) => void;
}

/** Destination visibility never authorizes Simulation or broker orders. */
export function SpectrumNavigation({ workspace, onSelect }: SpectrumNavigationProps) {
  const options = useExperienceOptions(s => s.options);
  const view = useView();
  const status = useSimStatus();
  const simulationAllowed = view === 'simulation' || status.isSuccess ||
    (status.isError && !isOwnerDenied(status.error));
  const visible = options.spectrumVisible;
  const selected = options.spectrumOrder.filter(key => visible[key]);
  const navigable = selected.filter(key => key !== 'simulation' || simulationAllowed);
  const current: SpectrumProduct = workspace === 'radio' ? 'radio' : workspace === 'tv' ? 'tv' :
    view === 'simulation' ? 'simulation' : 'kite';
  const active = navigable.includes(current) ? current : navigable[0] || selected[0] || 'kite';
  const navigate = (key: SpectrumProduct) =>
    key === 'tv' ? onSelect('tv') : key === 'radio' ? onSelect('radio') : onSelect('kite', key === 'simulation' ? 'simulation' : 'live');
  const label = (key: SpectrumProduct) =>
    key === 'tv' ? options.tvLabel : key === 'radio' ? options.radioLabel : key === 'simulation' ? 'Simulation' : 'KITE';

  if (options.spectrumLayout === 'cycle') {
    const currentLabel = label(active);
    return <button type="button" data-testid="spectrum-cycle-switch"
      data-active-view={active === 'simulation' ? 'simulation' : 'live'}
      className="mobile-view-toggle unified-view-toggle"
      aria-label={`Toggle Spectrum: ${currentLabel}`}
      title={navigable.length > 1 ? 'Switch to next selected workspace' : currentLabel}
      disabled={navigable.length < 2}
      onClick={() => navigate(navigable[(navigable.indexOf(active) + 1) % navigable.length])}>
      {currentLabel}
    </button>;
  }
  return <>{selected.map(key => <button key={key} type="button"
    className="mobile-view-toggle unified-view-toggle"
    data-testid={`spectrum-${key}-tab`}
    aria-current={current === key ? 'page' : undefined}
    aria-label={label(key)} title={key === 'simulation' && !simulationAllowed ?
      'Simulation requires owner authorization' : label(key)}
    disabled={key === 'simulation' && !simulationAllowed}
    onClick={() => navigate(key)}>{label(key)}</button>)}</>;
}

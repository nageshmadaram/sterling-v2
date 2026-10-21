import { useEngineConfig, usePatchEngineConfig } from './useSterlingKiteEngine';
import { useGammaMoveConfig, useUpdateGammaMove } from './useGammaMove';
import { useSnapbackConfig, useUpdateSnapback } from './useSnapback';
import { useReleaseEngineEnabled, type EngineToggleId } from './useEngineToggles';

export type AlgoToggleId = EngineToggleId;

export interface AlgoToggle {
  id: AlgoToggleId;
  label: string;
  /** This strategy's own arming bit (automatic entry requested). */
  enabled: boolean;
  /** The strategy's engine is running. A stopped engine cannot be armed. */
  engineEnabled: boolean;
  pending: boolean;
  toggle: (() => void) | null;
  description: string;
}

/**
 * Per-strategy automatic-entry arming for SuperTrend, Gamma Unwind and Snapback.
 * Each switch only REQUESTS entries; the shared master AUTO switch and the risk and
 * safety gates stay authoritative.
 */
export function useAlgoToggles(): AlgoToggle[] {
  const engineOn = useReleaseEngineEnabled();

  const st = useEngineConfig();
  const stSet = usePatchEngineConfig();
  const gm = useGammaMoveConfig();
  const gmSet = useUpdateGammaMove();
  const sb = useSnapbackConfig();
  const sbSet = useUpdateSnapback();

  const stAuto = st.data ? st.data.supertrend_auto_execute !== false : false;
  const gmAuto = !!(gm.data?.config as { auto_execute?: boolean } | undefined)?.auto_execute;
  const sbAuto = sb.data?.config?.auto_execute === true;

  return [

    {
      id: 'supertrend',
      label: 'SuperTrend',
      enabled: stAuto,
      engineEnabled: engineOn.supertrend,
      pending: stSet.isPending,
      toggle: st.data ? () => stSet.mutate({ supertrend_auto_execute: !stAuto }) : null,
      description: stAuto
        ? 'Auto entry requested for ready SuperTrend signals. Safety gates still apply.'
        : 'Off. SuperTrend signals are manual only.',
    },
    {
      id: 'gamma_move',
      label: 'Gamma Unwind',
      enabled: gmAuto,
      engineEnabled: engineOn.gamma_move,
      pending: gmSet.isPending,
      toggle: gm.data ? () => gmSet.mutate({ auto_execute: !gmAuto }) : null,
      description: gmAuto
        ? 'Auto entry requested for ENTRY-tier rows only. Safety gates still apply.'
        : 'Off. ENTRY rows can be armed manually; watchlist rows never add exposure.',
    },
    {
      id: 'snapback',
      label: 'Snapback',
      enabled: sbAuto,
      engineEnabled: engineOn.snapback,
      pending: sbSet.isPending,
      toggle: sb.data ? () => sbSet.mutate({ auto_execute: !sbAuto }) : null,
      description: sbAuto
        ? 'Auto entry requested for armed Snapback plans. Safety gates still apply.'
        : 'Off. Snapback plans are manual only.',
    },
  ];
}

export default useAlgoToggles;

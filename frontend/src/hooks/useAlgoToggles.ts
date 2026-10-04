import { useEngineConfig, usePatchEngineConfig } from './useSterlingKiteEngine';
import { useNavigatorConfig, useSetNavigatorConfig } from './useNavigator';
import { useGammaMoveConfig, useUpdateGammaMove } from './useGammaMove';
import { useAdaptiveEdgeEngineConfig, useSetAdaptiveEdgeEngineConfig } from './useAdaptiveEdge';
import { useIntradayConfig, useUpdateIntraday } from './useIntraday';
import { useEngineEnabled, type EngineToggleId } from './useEngineToggles';

// Snapback deliberately has no broker execution path. Its stored auto_execute
// field belongs to research/frozen-config compatibility and must not be rendered
// as an actionable execution switch.
export type AlgoToggleId = Exclude<EngineToggleId, 'snapback'>;

export interface AlgoToggle {
  id: AlgoToggleId;
  label: string;
  enabled: boolean;
  engineEnabled: boolean;
  pending: boolean;
  toggle: (() => void) | null;
  description: string;
}

export function useAlgoToggles(): AlgoToggle[] {
  const engineOn = useEngineEnabled();

  const st = useEngineConfig();
  const stSet = usePatchEngineConfig();

  const nav = useNavigatorConfig();
  const navSet = useSetNavigatorConfig();

  const gm = useGammaMoveConfig();
  const gmSet = useUpdateGammaMove();

  const ae = useAdaptiveEdgeEngineConfig();
  const aeSet = useSetAdaptiveEdgeEngineConfig();

  const intraday = useIntradayConfig();
  const intradaySet = useUpdateIntraday();

  const stAuto = st.data ? st.data.supertrend_auto_execute !== false : false;
  const navRecord = nav.data?.record;
  const navAuto = !!navRecord?.config.auto_execute_originated;
  const gmAuto = !!(gm.data?.config as { auto_execute?: boolean } | undefined)?.auto_execute;
  const aeAuto = !!(ae.data?.config as { auto_execute?: boolean } | undefined)?.auto_execute;
  const intradayAuto = !!intraday.data?.config?.auto_execute;

  return [
    {
      id: 'supertrend',
      label: 'SuperTrend',
      enabled: stAuto,
      engineEnabled: engineOn.supertrend,
      pending: stSet.isPending,
      toggle: st.data ? () => stSet.mutate({ supertrend_auto_execute: !stAuto }) : null,
      description: stAuto
        ? 'Auto-entry requested for ready SuperTrend signals; execution safety gates still apply.'
        : 'Off — manual order placement only for SuperTrend signals.',
    },
    {
      id: 'navigator',
      label: 'Value-Flow Navigator',
      enabled: navAuto,
      engineEnabled: engineOn.navigator,
      pending: navSet.isPending,
      toggle: navRecord
        ? () => navSet.mutate({
            config: { ...navRecord.config, auto_execute_originated: !navAuto },
            expected_revision: navRecord.revision,
          })
        : null,
      description: navAuto
        ? 'Auto-entry requested for Navigator-originated setups; execution safety gates still apply.'
        : 'Off — manual order placement only for Navigator setups.',
    },
    {
      id: 'gamma_move',
      label: 'Gamma Unwind',
      enabled: gmAuto,
      engineEnabled: engineOn.gamma_move,
      pending: gmSet.isPending,
      toggle: gm.data ? () => gmSet.mutate({ auto_execute: !gmAuto }) : null,
      description: gmAuto
        ? 'Auto-entry requested for Gamma-Unwind ENTRY tier only; canonical execution and account safety can still refuse.'
        : 'Off — ENTRY rows remain manually armable; STRONG WATCHLIST and WATCHLIST never increase exposure.',
    },
    {
      id: 'adaptive_edge',
      label: 'Adaptive Edge',
      enabled: aeAuto,
      engineEnabled: engineOn.adaptive_edge,
      pending: aeSet.isPending,
      toggle: ae.data
        ? () => aeSet.mutate({ auto_execute: !aeAuto })
        : null,
      description: aeAuto
        ? 'Auto-entry requested for Adaptive Edge; its promotion/formula gates can still refuse.'
        : 'Off — manual order placement only for Adaptive Edge candidates.',
    },
    {
      id: 'intraday',
      label: 'Intraday pack',
      enabled: intradayAuto,
      engineEnabled: engineOn.intraday,
      pending: intradaySet.isPending,
      toggle: intraday.data
        ? () => intradaySet.mutate({ auto_execute: !intradayAuto })
        : null,
      description: intradayAuto
        ? 'Auto-entry requested for the Intraday pack; each strategy must still pass its walk-forward validation gate.'
        : 'Off — Intraday signals remain manually armable; unattended entry is not requested.',
    },
  ];
}

export default useAlgoToggles;

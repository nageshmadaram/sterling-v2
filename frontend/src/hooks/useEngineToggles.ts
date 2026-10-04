import { useEngineConfig, usePatchEngineConfig } from './useSterlingKiteEngine';
import { useNavigatorConfig, useSetNavigatorConfig } from './useNavigator';
import { useGammaMoveConfig, useUpdateGammaMove } from './useGammaMove';
import { useAdaptiveEdgeEngineConfig, useSetAdaptiveEdgeEngineConfig } from './useAdaptiveEdge';
import { useIntradayConfig, useUpdateIntraday } from './useIntraday';
import { useSnapbackConfig, useUpdateSnapback } from './useSnapback';

export type EngineToggleId =
  | 'supertrend' | 'navigator' | 'gamma_move' | 'adaptive_edge' | 'intraday'
  | 'snapback';

export interface EngineToggle {
  id: EngineToggleId;
  label: string;
  enabled: boolean;
  pending: boolean;
  toggle: (() => void) | null;
  description: string;
}

export function useEngineEnabled(): Record<EngineToggleId, boolean> {
  const st = useEngineConfig();
  const nav = useNavigatorConfig();
  const gm = useGammaMoveConfig();
  const ae = useAdaptiveEdgeEngineConfig();
  const id = useIntradayConfig();
  const sb = useSnapbackConfig();

  // Missing/unreadable config is UNKNOWN, never ON. Rendering UNKNOWN as
  // enabled makes a failed query indistinguishable from an operator choice and
  // can cause the shared re-scan sweep to spend broker budget on an engine whose
  // state was never verified. Every engine therefore uses the same fail-closed
  // rule: only an explicit authoritative true is ON.
  return {
    supertrend: st.data?.engine_enabled === true,
    navigator: nav.data?.record?.config.enabled === true,
    gamma_move: (gm.data?.config as { enabled?: boolean } | undefined)?.enabled === true,
    adaptive_edge: (ae.data?.config as { enabled?: boolean } | undefined)?.enabled === true,
    intraday: id.data?.config?.enabled === true,
    snapback: sb.data?.config?.enabled === true,
  };
}

export function useEngineToggles(): EngineToggle[] {
  const on = useEngineEnabled();
  const st = useEngineConfig();
  const stSet = usePatchEngineConfig();

  const nav = useNavigatorConfig();
  const navSet = useSetNavigatorConfig();

  const gm = useGammaMoveConfig();
  const gmSet = useUpdateGammaMove();

  const ae = useAdaptiveEdgeEngineConfig();
  const aeSet = useSetAdaptiveEdgeEngineConfig();

  const idCfg = useIntradayConfig();
  const idSet = useUpdateIntraday();

  const sbCfg = useSnapbackConfig();
  const sbSet = useUpdateSnapback();

  const stOn = on.supertrend;
  const navRecord = nav.data?.record;
  const navOn = on.navigator;
  const gmOn = on.gamma_move;
  const aeOn = on.adaptive_edge;
  const idOn = on.intraday;
  const sbOn = on.snapback;

  return [
    {
      id: 'supertrend',
      label: 'SuperTrend engine',
      enabled: stOn,
      pending: stSet.isPending,
      toggle: st.data ? () => stSet.mutate({ engine_enabled: !stOn }) : null,
      description: stOn
        ? 'Scanning and producing SuperTrend signals. Automatic entry is controlled separately.'
        : 'Off — no SuperTrend scanning and no SuperTrend signals.',
    },
    {
      id: 'navigator',
      label: 'Value-Flow Navigator',
      enabled: navOn,
      pending: navSet.isPending,
      toggle: navRecord
        ? () => navSet.mutate({
            config: { ...navRecord.config, enabled: !navOn },
            expected_revision: navRecord.revision,
          })
        : null,
      description: navOn
        ? 'On. It can confirm SuperTrend setups and originate its own.'
        : 'Off — no Navigator evidence and no Navigator-originated setups.',
    },
    {
      id: 'gamma_move',
      label: 'Gamma Unwind',
      enabled: gmOn,
      pending: gmSet.isPending,
      toggle: gm.data ? () => gmSet.mutate({ enabled: !gmOn }) : null,
      description: gmOn
        ? 'Daily zones → HOI-1 watchlists → hidden 5-minute E2 → live E1 → four-gate tiering.'
        : 'Off — no new Gamma-Unwind watchlists or E1 signals; existing positions remain reconciled and managed.',
    },
    {
      id: 'adaptive_edge',
      label: 'Adaptive Edge',
      enabled: aeOn,
      pending: aeSet.isPending,
      toggle: ae.data ? () => aeSet.mutate({ enabled: !aeOn }) : null,
      description: aeOn
        ? 'Order-flow scalping. Paper entries run through canonical execution; live entry remains promotion/evidence gated.'
        : 'Off — no new order-flow scans or Adaptive Edge candidates; existing positions remain managed.',
    },
    {
      id: 'intraday',
      label: 'Intraday pack',
      enabled: idOn,
      pending: idSet.isPending,
      toggle: idCfg.data ? () => idSet.mutate({ enabled: !idOn }) : null,
      description: idOn
        ? 'Pivot Break, MA Ribbon and VWAP SuperTrend on 5-minute candles. Manual/paper arming is available; unattended entry requires each strategy to pass walk-forward validation.'
        : 'Off — no new Intraday scans or signals; existing positions still run exits, reconciliation and session-end safety.',
    },
    {
      id: 'snapback',
      label: 'Snapback',
      enabled: sbOn,
      pending: sbSet.isPending,
      toggle: sbCfg.data ? () => sbSet.mutate({ enabled: !sbOn }) : null,
      description: sbOn
        ? 'Buys puts into a stretched 20-session breakout, on daily bars. '
          + 'Signals only — it has not cleared the walk-forward gate.'
        : 'Off — no daily scan and no Snapback signals.',
    },
  ];
}

export default useEngineToggles;

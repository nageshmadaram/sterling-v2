import { useEngineConfig, usePatchEngineConfig } from './useSterlingKiteEngine';
import { useGammaMoveConfig, useUpdateGammaMove } from './useGammaMove';
import { useSnapbackConfig, useUpdateSnapback } from './useSnapback';

/**
 * Running switches for the three release strategies.
 *
 * Missing or unreadable config is UNKNOWN, never ON. Treating UNKNOWN as enabled makes
 * a failed query look like an operator choice and can send the shared re-scan to an
 * engine whose state was never verified, so only an explicit authoritative `true` is ON.
 */
export type EngineToggleId = 'supertrend' | 'gamma_move' | 'snapback' | 'ema_trend';
export type ReleaseEngineToggleId = EngineToggleId;

export interface EngineToggle {
  id: EngineToggleId;
  label: string;
  enabled: boolean;
  pending: boolean;
  toggle: (() => void) | null;
  description: string;
}

export function useReleaseEngineEnabled(): Record<EngineToggleId, boolean> {
  const st = useEngineConfig();
  const gm = useGammaMoveConfig();
  const sb = useSnapbackConfig();

  return {
    supertrend: st.data?.engine_enabled === true,
    gamma_move: (gm.data?.config as { enabled?: boolean } | undefined)?.enabled === true,
    snapback: sb.data?.config?.enabled === true,
    // EMA Trend is a paper-only research engine, outside the release. Never on here.
    ema_trend: false,
  };
}

export function useReleaseEngineToggles(): EngineToggle[] {
  const st = useEngineConfig();
  const stSet = usePatchEngineConfig();
  const gm = useGammaMoveConfig();
  const gmSet = useUpdateGammaMove();
  const sb = useSnapbackConfig();
  const sbSet = useUpdateSnapback();
  const on = useReleaseEngineEnabled();

  return [

    {
      id: 'supertrend',
      label: 'SuperTrend engine',
      enabled: on.supertrend,
      pending: stSet.isPending,
      toggle: st.data ? () => stSet.mutate({ engine_enabled: !on.supertrend }) : null,
      description: on.supertrend
        ? 'Scanning and producing SuperTrend signals. Automatic entry is armed separately.'
        : 'Off. No SuperTrend scans or signals.',
    },
    {
      id: 'gamma_move',
      label: 'Gamma Unwind',
      enabled: on.gamma_move,
      pending: gmSet.isPending,
      toggle: gm.data ? () => gmSet.mutate({ enabled: !on.gamma_move }) : null,
      description: on.gamma_move
        ? 'Scanning for Gamma Unwind setups. Automatic entry is armed separately.'
        : 'Off. No new Gamma Unwind setups; open positions stay managed.',
    },
    {
      id: 'snapback',
      label: 'Snapback',
      enabled: on.snapback,
      pending: sbSet.isPending,
      toggle: sb.data ? () => sbSet.mutate({ enabled: !on.snapback }) : null,
      description: on.snapback
        ? 'Scanning for Snapback setups. Automatic entry is armed separately.'
        : 'Off. No Snapback scans or signals.',
    },
  ];
}

export default useReleaseEngineToggles;

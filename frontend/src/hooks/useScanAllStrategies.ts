import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useRunScan } from './useSterlingKiteEngine';
import { useRunNavigatorScan } from './useNavigator';
import { useGammaMoveScan } from './useGammaMove';
import { useIntradayScan } from './useIntraday';
import { useRunSnapbackScan } from './useSnapback';
import { useScanActivity } from '../store/useScanActivity';

export type ScannableEngine =
  | 'supertrend' | 'navigator' | 'gamma_move' | 'adaptive_edge' | 'intraday' | 'snapback';

export const SCANNABLE_ENGINE_LABEL: Record<ScannableEngine, string> = {
  supertrend: 'SuperTrend',
  navigator: 'Navigator',
  gamma_move: 'Gamma Unwind',
  adaptive_edge: 'Adaptive Edge',
  intraday: 'Intraday',
  snapback: 'Snapback',
};

export interface EngineScanResult {
  engine: ScannableEngine;
  ok: boolean;
  /** Present when the scan was refused or failed. */
  error?: string;
}


export type ReleaseScannableEngine = 'supertrend' | 'gamma_move' | 'snapback';

export const RELEASE_SCANNABLE_ENGINE_LABEL: Record<ReleaseScannableEngine, string> = {
  supertrend: 'SuperTrend',
  gamma_move: 'Gamma Unwind',
  snapback: 'Snapback',
};

/** `POST /config/adaptive-edge/scan`. Had no hook before this. */
export function useAdaptiveEdgeScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/v1/config/adaptive-edge/scan', {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['adaptive-edge-snapshot'] }),
  });
}

export function useScanReleaseStrategies() {
  const cancelRequested = useRef(false);
  const supertrend = useRunScan();
  const gammaMove = useGammaMoveScan();
  const snapback = useRunSnapbackScan();

  const runners: Record<ReleaseScannableEngine, () => Promise<unknown>> = {
    supertrend: () => supertrend.mutateAsync(),
    gamma_move: () => gammaMove.mutateAsync(),
    snapback: () => snapback.mutateAsync(),
  };

  const scanAll = async (
    order: readonly ReleaseScannableEngine[],
  ): Promise<EngineScanResult[]> => {
    cancelRequested.current = false;
    const results: EngineScanResult[] = [];
    const setCurrent = useScanActivity.getState().setCurrent;
    try {
      for (const engine of order) {
        if (cancelRequested.current) break;
        setCurrent(engine);
        try {
          await runners[engine]();
          results.push({ engine, ok: true });
        } catch (err) {
          results.push({
            engine,
            ok: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    } finally {
      setCurrent(null);
    }
    return results;
  };

  const cancelAll = () => {
    // Gamma-Unwind and Snapback do not expose a safe mid-request abort.
    // Let the current request finish and prevent the next release strategy
    // from starting. SuperTrend's caller may additionally invoke its server
    // cancel endpoint.
    cancelRequested.current = true;
  };

  return {
    scanAll,
    cancelAll,
    isPending: supertrend.isPending || gammaMove.isPending || snapback.isPending,
  };
}

export function useScanAllStrategies() {
  const cancelRequested = useRef(false);
  const supertrend = useRunScan();
  const navigator = useRunNavigatorScan();
  const gammaMove = useGammaMoveScan();
  const adaptiveEdge = useAdaptiveEdgeScan();
  const intraday = useIntradayScan();
  const snapback = useRunSnapbackScan();

  const runners: Record<ScannableEngine, () => Promise<unknown>> = {
    supertrend: () => supertrend.mutateAsync(),
    navigator: () => navigator.mutateAsync(),
    gamma_move: () => gammaMove.mutateAsync(),
    adaptive_edge: () => adaptiveEdge.mutateAsync(),
    intraday: () => intraday.mutateAsync(),
    snapback: () => snapback.mutateAsync(),
  };

  const scanAll = async (order: readonly ScannableEngine[]): Promise<EngineScanResult[]> => {
    cancelRequested.current = false;
    const results: EngineScanResult[] = [];
    const setCurrent = useScanActivity.getState().setCurrent;
    try {
      for (const engine of order) {
        if (cancelRequested.current) break;
        setCurrent(engine);
        try {
          await runners[engine]();
          results.push({ engine, ok: true });
        } catch (err) {
          results.push({
            engine,
            ok: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    } finally {
      setCurrent(null);
    }
    return results;
  };

  const cancelAll = () => {
    // Gamma/Adaptive/Intraday/Snapback do not expose a safe mid-request abort.
    // Mark the sweep cancelled so the current request may finish but no later
    // strategy starts. SuperTrend/Navigator have their own server-side cancel
    // endpoints and the caller invokes those in addition to this queue stop.
    cancelRequested.current = true;
  };

  return {
    scanAll,
    cancelAll,
    isPending: supertrend.isPending || navigator.isPending
      || gammaMove.isPending || adaptiveEdge.isPending || intraday.isPending || snapback.isPending,
  };
}

export default useScanAllStrategies;

import { useRef } from 'react';
import { useScanActivity } from '../store/useScanActivity';
import { useRunScan } from './useSterlingKiteEngine';
import { useGammaMoveScan } from './useGammaMove';
import { useRunSnapbackScan } from './useSnapback';

export type ScannableEngine = 'supertrend' | 'gamma_move' | 'snapback';

export const SCANNABLE_ENGINE_LABEL: Record<ScannableEngine, string> = {
  supertrend: 'SuperTrend',
  gamma_move: 'Gamma Unwind',
  snapback: 'Snapback',
};

export interface EngineScanResult {
  engine: ScannableEngine;
  ok: boolean;
  /** Present when the scan was refused or failed. */
  error?: string;
}

/**
 * One manual re-scan across the three release strategies, run one at a time.
 * They share one historical-data budget, so firing them together makes each slower
 * rather than the set faster.
 */
export function useScanStrategies() {
  const cancelRequested = useRef(false);
  const supertrend = useRunScan();
  const gammaMove = useGammaMoveScan();
  const snapback = useRunSnapbackScan();

  const runners: Record<ScannableEngine, () => Promise<unknown>> = {
    supertrend: () => supertrend.mutateAsync(),
    gamma_move: () => gammaMove.mutateAsync(),
    snapback: () => snapback.mutateAsync(undefined),
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
    // Gamma Unwind and Snapback do not expose a safe mid-request abort. Let the
    // current request finish and stop the next strategy from starting. SuperTrend's
    // caller may additionally call its server cancel endpoint.
    cancelRequested.current = true;
  };

  return {
    scanAll,
    cancelAll,
    isPending: supertrend.isPending || gammaMove.isPending || snapback.isPending,
  };
}

export default useScanStrategies;

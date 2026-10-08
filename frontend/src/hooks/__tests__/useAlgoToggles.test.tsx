import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAlgoToggles } from '../useAlgoToggles';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const supertrendMutate = vi.hoisted(() => vi.fn());
const gammaMutate = vi.hoisted(() => vi.fn());
const snapbackMutate = vi.hoisted(() => vi.fn());

vi.mock('../useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true, auto_execute: true, supertrend_auto_execute: true } }),
  usePatchEngineConfig: () => ({ mutate: supertrendMutate, isPending: false }),
}));

vi.mock('../useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: { config: { enabled: true, auto_execute: false } } }),
  useUpdateGammaMove: () => ({ mutate: gammaMutate, isPending: false }),
}));

vi.mock('../useSnapback', () => ({
  useSnapbackConfig: () => ({ data: { config: { enabled: true, auto_execute: true } } }),
  useUpdateSnapback: () => ({ mutate: snapbackMutate, isPending: false }),
}));

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useAlgoToggles(), { wrapper });
}

describe('useAlgoToggles', () => {
  it('returns exactly the three release strategies', () => {
    const { result } = setup();
    expect(result.current.map((t) => t.id)).toEqual(['supertrend', 'gamma_move', 'snapback']);
    for (const retired of ['navigator', 'adaptive_edge', 'intraday']) {
      expect(result.current.map((t) => t.id)).not.toContain(retired);
    }
  });

  it('reports each strategy’s own arming bit', () => {
    const { result } = setup();
    expect(result.current.find((t) => t.id === 'supertrend')?.enabled).toBe(true);
    expect(result.current.find((t) => t.id === 'gamma_move')?.enabled).toBe(false);
    expect(result.current.find((t) => t.id === 'snapback')?.enabled).toBe(true);
  });

  it('SuperTrend strategy toggle does not switch off master AUTO', () => {
    supertrendMutate.mockClear();
    const { result } = setup();

    result.current.find((t) => t.id === 'supertrend')?.toggle?.();

    expect(supertrendMutate).toHaveBeenCalledTimes(1);
    expect(supertrendMutate).toHaveBeenCalledWith({ supertrend_auto_execute: false });
    expect(supertrendMutate).not.toHaveBeenCalledWith({ auto_execute: false });
  });

  it('sends only auto_execute for Gamma Unwind and Snapback', () => {
    gammaMutate.mockClear();
    snapbackMutate.mockClear();
    const { result } = setup();

    result.current.find((t) => t.id === 'gamma_move')?.toggle?.();
    result.current.find((t) => t.id === 'snapback')?.toggle?.();

    expect(gammaMutate).toHaveBeenCalledWith({ auto_execute: true });
    expect(snapbackMutate).toHaveBeenCalledWith({ auto_execute: false });
  });
});

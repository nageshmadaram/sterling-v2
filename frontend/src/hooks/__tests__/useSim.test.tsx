import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const get = vi.fn();
const put = vi.fn();
const post = vi.fn();
vi.mock('../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), put: (...a: unknown[]) => put(...a), post: (...a: unknown[]) => post(...a) } }));

import {
  SIM_STATUS_KEY, STALL_AFTER_MS, describeSimError, isOwnerDenied, modeOf, simRunState, simulatedStrategies,
  useResetSimulation, useSetSimCapital, useSetStrategyMode, useSimStatus, type SimStatus,
} from '../useSim';
import { useViewStore } from '../../store/useViewStore';

const NOW = 1_800_000_000_000;

function status(over: Partial<SimStatus> = {}): SimStatus {
  return {
    virtual: true, capital: 1_000_000, cash: 900_000, equity: 1_002_500, today_pnl: 2_500, open_positions: 1,
    runtime: { running: true, last_pass_ms: NOW - 2_000, session_open: true },
    price_feed: { available: true, detail: '' },
    strategies: {},
    ...over,
  };
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

const apiError = (status: number, detail: unknown) => {
  const e = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)) as Error & { status: number };
  e.status = status;
  return e;
};

beforeEach(() => { [get, put, post].forEach((m) => m.mockReset()); useViewStore.setState({ view: 'live' }); });
afterEach(() => { useViewStore.setState({ view: 'live' }); });

describe('useSimStatus', () => {
  it('reads GET /api/v1/sim/status', async () => {
    get.mockResolvedValue(status());
    const { wrapper } = setup();
    const { result } = renderHook(() => useSimStatus(), { wrapper });
    await waitFor(() => expect(result.current.data?.equity).toBe(1_002_500));
    expect(get).toHaveBeenCalledWith('/api/v1/sim/status');
  });

  it('does not fetch when disabled', () => {
    const { wrapper } = setup();
    renderHook(() => useSimStatus(false), { wrapper });
    expect(get).not.toHaveBeenCalled();
  });
});

describe('mutations', () => {
  it('PUTs the mode to the strategy route and takes the answer\'s strategies into the status', async () => {
    const { client, wrapper } = setup();
    client.setQueryData(SIM_STATUS_KEY, status());
    put.mockResolvedValue({ strategy: 'gamma', mode: 'SIMULATION', strategies: { gamma: { strategy: 'gamma', mode: 'SIMULATION' } } });
    const { result } = renderHook(() => useSetStrategyMode(), { wrapper });
    await act(async () => { await result.current.mutateAsync({ strategy: 'gamma', mode: 'SIMULATION' }); });
    expect(put).toHaveBeenCalledWith('/api/v1/sim/strategy/gamma/mode', { mode: 'SIMULATION' });
    expect(modeOf(client.getQueryData<SimStatus>(SIM_STATUS_KEY), 'gamma')).toBe('SIMULATION');
  });

  it('resets with a JSON true confirm and keeps the status it returns', async () => {
    const { client, wrapper } = setup();
    post.mockResolvedValue({ ok: true, status: status({ cash: 1_000_000, today_pnl: 0, open_positions: 0 }) });
    const { result } = renderHook(() => useResetSimulation(), { wrapper });
    await act(async () => { await result.current.mutateAsync(); });
    expect(post).toHaveBeenCalledWith('/api/v1/sim/reset', { confirm: true });
    expect(client.getQueryData<SimStatus>(SIM_STATUS_KEY)?.open_positions).toBe(0);
  });

  it('sets the virtual capital with PUT /sim/capital', async () => {
    const { client, wrapper } = setup();
    put.mockResolvedValue(status({ capital: 500_000 }));
    const { result } = renderHook(() => useSetSimCapital(), { wrapper });
    await act(async () => { await result.current.mutateAsync(500_000); });
    expect(put).toHaveBeenCalledWith('/api/v1/sim/capital', { capital: 500_000 });
    expect(client.getQueryData<SimStatus>(SIM_STATUS_KEY)?.capital).toBe(500_000);
  });
});

describe('describeSimError', () => {
  it('lists the readiness blockers on a 409', () => {
    const v = describeSimError(apiError(409, {
      error: 'auto_execute_blocked', code: 'auto_execute_blocked', message: 'blocked',
      reasons: ['Broker session is not logged in', 'No stop-loss protection configured'],
    }));
    expect(v.reasons).toEqual(['Broker session is not logged in', 'No stop-loss protection configured']);
    expect(v.text).toMatch(/Fix these first/);
  });

  it('says the virtual account is unavailable on a 503', () => {
    const v = describeSimError(apiError(503, { code: 'tenant_unavailable', message: 'x' }));
    expect(v.text).toMatch(/not available right now/i);
    expect(v.reasons).toEqual([]);
  });

  it('says a half-done switch was left OFF on a 500', () => {
    const v = describeSimError(apiError(500, { code: 'mode_change_failed', message: 'x' }));
    expect(v.text).toMatch(/left OFF/);
  });

  it('explains a 403 from the Simulation view and an owner-only 403', () => {
    expect(describeSimError(apiError(403, 'Not available in Simulation view')).text).toMatch(/Switch to LIVE/);
    expect(describeSimError(apiError(403, { code: 'owner_only', message: 'x' })).text).toMatch(/owner/);
  });

  it('keeps a plain 400/422 message and explains a dead network', () => {
    expect(describeSimError(apiError(422, { code: 'invalid_capital', message: 'capital must be positive' })).text)
      .toBe('capital must be positive');
    expect(describeSimError(new TypeError('Failed to fetch')).text).toMatch(/Could not reach/);
  });

  it('never prints raw JSON', () => {
    const v = describeSimError(apiError(418, { code: 'teapot' }));
    expect(v.text).not.toContain('{');
  });
});

describe('isOwnerDenied', () => {
  it('is true for a 403 and only for a 403', () => {
    expect(isOwnerDenied(apiError(403, 'Simulation view is available to the owner only'))).toBe(true);
    expect(isOwnerDenied(apiError(503, { code: 'tenant_unavailable' }))).toBe(false);
    expect(isOwnerDenied(null)).toBe(false);
  });
});

describe('simRunState', () => {
  it('is running when a pass landed a moment ago', () => {
    expect(simRunState(status(), NOW)).toMatchObject({ key: 'running', tone: 'good', label: 'Running' });
  });

  it('is stalled when the last pass is old, with the reason it gave', () => {
    const s = status({ runtime: { running: true, last_pass_ms: NOW - STALL_AFTER_MS - 5_000, session_open: true, last_error: 'quote timeout' } });
    const r = simRunState(s, NOW);
    expect(r).toMatchObject({ key: 'stalled', tone: 'warn' });
    expect(r.detail).toMatch(/35 seconds/);
    expect(r.detail).toMatch(/quote timeout/);
  });

  it('is stopped when the loop is not running', () => {
    expect(simRunState(status({ runtime: { running: false, last_pass_ms: 0, session_open: true } }), NOW).key).toBe('stopped');
  });

  it('is market closed outside the session, however old the last pass is', () => {
    const s = status({ runtime: { running: true, last_pass_ms: NOW - 3_600_000, session_open: false } });
    expect(simRunState(s, NOW).key).toBe('closed');
  });

  it('is paused when prices cannot flow, and says why', () => {
    const s = status({ price_feed: { available: false, detail: 'Broker session is not logged in: simulation is paused.' } });
    const r = simRunState(s, NOW);
    expect(r.key).toBe('paused');
    expect(r.detail).toMatch(/not logged in/);
  });

  it('is starting before the first pass, and while the status is loading', () => {
    expect(simRunState(status({ runtime: { running: true, last_pass_ms: 0, session_open: true } }), NOW).key).toBe('starting');
    expect(simRunState(undefined, NOW).key).toBe('starting');
  });
});

describe('mode helpers', () => {
  it('reads a strategy\'s mode and lists the simulated ones in display order', () => {
    const s = status({ strategies: {
      gamma: { strategy: 'gamma', mode: 'SIMULATION' } as never,
      supertrend: { strategy: 'supertrend', mode: 'SIMULATION' } as never,
      snapback: { strategy: 'snapback', mode: 'AUTO' } as never,
    } });
    expect(modeOf(s, 'snapback')).toBe('AUTO');
    expect(modeOf(undefined, 'snapback')).toBe('MANUAL');
    expect(simulatedStrategies(s)).toEqual(['supertrend', 'gamma']);
  });
});

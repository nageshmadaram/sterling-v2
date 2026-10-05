import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { GammaMoveSettings } from '../GammaMoveSettings';
import payload from './fixtures/gamma-move.json';

function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

let cfgQuery: any;
let updateState: any;

vi.mock('../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => cfgQuery,
  useUpdateGammaMove: () => updateState,
}));

beforeEach(() => {
  cfgQuery = { isLoading: false, data: JSON.parse(JSON.stringify(payload)) };
  updateState = { mutate: vi.fn(), isPending: false };
  localStorage.clear();
});

describe('GammaMoveSettings / Gamma Unwind', () => {
  it('states the uploaded reference identity and execution separation', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    expect(text).toContain('GAMMA-UNWIND client 0.8.2');
    expect(text).toContain('S1.6.0');
    expect(text).toContain('Execution is an adapter, not part of the supplied client');
    expect(text).toContain('Only ENTRY can execute');
  });

  it('shows every reference settings family', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    for (const title of [
      'Execution adapter', 'Instruments', 'Daily zones', 'Watchlist and strike',
      'Watcher', 'Four gates', 'Trade follow-up', 'Schedule',
    ]) expect(text).toContain(title);
  });

  it('shows the client-fixed invariants instead of pretending they are editable', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    expect(text).toContain('1m base');
    expect(text).toContain('5m signal');
    expect(text).toContain('15m display');
    expect(text).toContain('effective HOI-1');
    expect(text).toContain('strategy.yaml top_n_oi');
  });

  it('exposes the reference watch, gate, hold and schedule settings', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    for (const label of [
      'OI drop alert', 'OI tolerance', 'Strong OI drop', 'Re-arm extra drop',
      'Volume SMA', 'Consolidation bars', 'Base zone gap', 'Liquidity bars',
      'Minimum average volume', 'Unwind ticks', 'Swing trading days',
      'Late-day warning', 'Expiry warning', 'Expiry switch threshold',
    ]) expect(text).toContain(label);
  });

  it('drafts settings locally and saves only on Apply', () => {
    render(<GammaMoveSettings />);
    const executionLots = screen.getByLabelText(/Execution lots/i);
    fireEvent.change(executionLots, { target: { value: '2' } });
    expect(updateState.mutate).not.toHaveBeenCalled();
    const apply = screen.getAllByRole('button').find((b) => /apply/i.test(b.textContent ?? ''));
    expect(apply).toBeTruthy();
    fireEvent.click(apply!);
    expect(updateState.mutate).toHaveBeenCalledTimes(1);
    expect(updateState.mutate.mock.calls[0][0].execution_lots).toBe(2);
  });

  it('shows a loading state rather than an empty panel', () => {
    cfgQuery = { isLoading: true, data: undefined };
    render(<GammaMoveSettings />);
    expect(screen.getByText(/Loading Gamma Unwind settings/)).toBeTruthy();
  });

  it('does not expose removed Gamma Move calibration/15-minute-trigger controls', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('level_proximity_pct');
    expect(text).not.toContain('min_price_gain_pct');
    expect(text).not.toContain('regime_multiplier');
    expect(text).not.toContain('15-minute OI bars');
  });
});

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EmaTrendSettings } from '../EmaTrendSettings';
import { emaConfig } from '../../test/emaTrendFixtures';
import { api } from '../../utils/api';
import type { EmaTrendConfig } from '../../types/emaTrend';
vi.mock('../../utils/api', () => ({ api: { get: vi.fn(), patch: vi.fn() } }));
let response = emaConfig();
function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={qc}><EmaTrendSettings /></QueryClientProvider>);
  return qc;
}
beforeEach(() => {
  vi.clearAllMocks(); response = emaConfig();
  vi.mocked(api.get).mockImplementation(async () => response as never);
  vi.mocked(api.patch).mockImplementation(async (_path, input) => {
    const body = input as { changes: Partial<EmaTrendConfig> };
    response = { ...response, config: { ...response.config, ...body.changes }, revision: response.revision + 1 };
    return response as never;
  });
});
afterEach(cleanup);
describe('EMA Trend Connect drafts', () => {
  it('saves only edits with CAS and allows typing separated symbols', async () => {
    show();
    const input = await screen.findByLabelText('EMA Trend instruments');
    fireEvent.change(input, { target: { value: 'NSE:TCS, NSE:RELIANCE' } });
    expect(input).toHaveValue('NSE:TCS, NSE:RELIANCE');
    fireEvent.change(screen.getByLabelText('Paper allocation (₹)'), { target: { value: '125000' } });
    expect(api.patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/api/v1/ema-trend/config', {
      expected_revision: 7, changes: { symbols: ['NSE:TCS', 'NSE:RELIANCE'], allocation_inr: 125000 },
    }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Apply changes' })).not.toBeInTheDocument());
  });
  it('retains the draft on stale revision rejection and discards without writing', async () => {
    vi.mocked(api.patch).mockRejectedValue(new Error('409 STALE_CONFIG_REVISION'));
    show();
    const input = await screen.findByLabelText('Paper allocation (₹)');
    fireEvent.change(input, { target: { value: '150000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('STALE_CONFIG_REVISION');
    expect(input).toHaveValue(150000);
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    expect(input).toHaveValue(100000);
    expect(api.patch).toHaveBeenCalledTimes(1);
  });
  it('checks Kite readiness only after explicit click and never labels it live-enabled', async () => {
    vi.mocked(api.get).mockImplementation(async path => path === '/api/v1/ema-trend/live-readiness'
      ? { status: 'LIVE_BLOCKED', account_id: 'KITE-TEST', live_orders_enabled: false, read_only: true,
          blockers: [], checks: { account_bound: true, kite_session_validated: true },
          modes: { swing: { product: 'CNC', eligible: false, blockers: ['CNC_HOLDINGS_AUTHORIZATION_NOT_VERIFIED'] } },
          requires_operator_certification: true } as never
      : response as never);
    show();
    await screen.findByLabelText('EMA Trend instruments');
    expect(api.get).not.toHaveBeenCalledWith('/api/v1/ema-trend/live-readiness');
    fireEvent.click(screen.getByRole('button', { name: 'Check Kite live readiness' }));
    expect(await screen.findByLabelText('EMA live readiness')).toHaveTextContent('LIVE BLOCKED');
    expect(screen.getByLabelText('EMA live readiness')).toHaveTextContent('Broker orders: not enabled');
    expect(api.get).toHaveBeenCalledWith('/api/v1/ema-trend/live-readiness');
  });
  it('stages spot index watches without changing existing instruments or placing orders', async () => {
    show();
    await screen.findByLabelText('EMA Trend instruments');
    fireEvent.click(screen.getByRole('checkbox', { name: 'NIFTY' }));
    expect(api.patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/api/v1/ema-trend/config', {
      expected_revision: 7, changes: { scan_indices: ['NIFTY 50'] },
    }));
    expect(response.config.symbols).toEqual(['NSE:TCS']);
    expect(response.config.auto_execute).toBe(false);
  });
  it('stages server defaults without changing the running backend until Apply', async () => {
    show();
    await screen.findByLabelText('EMA Trend instruments');
    fireEvent.click(screen.getByRole('button', { name: 'Stage server defaults' }));
    expect(screen.getByLabelText('Paper allocation (₹)')).toHaveValue(0);
    expect(screen.getByText('RUNNING')).toBeInTheDocument();
    expect(screen.getByText('WILL STOP ON APPLY')).toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

it('adds an actual broker option contract to the draft and saves only its exact symbol', async () => {
  vi.mocked(api.get).mockImplementation(async path => String(path).includes('/instruments?') ? { items: [
    { symbol: 'NFO:NIFTY26NOV25000CE', name: 'NIFTY', exchange: 'NFO', instrument_type: 'CE', expiry: '2026-11-26',
      lot_size: 25, tick_size: '.05', execution_supported: true, blocker: null,
      contract: { product: 'NRML', lot_size: 25 } },
    { symbol: 'MCX:GOLD26NOVFUT', name: 'GOLD', exchange: 'MCX', instrument_type: 'FUT', expiry: '2026-11-30',
      lot_size: 1, tick_size: '1', execution_supported: false, blocker: 'MCX_MULTIPLIER_CALENDAR_ACCOUNTING_NOT_IMPLEMENTED', contract: null },
  ] } as never : response as never);
  show();
  fireEvent.click(await screen.findByText(/Add more stocks or exact derivative contracts/));
  const search = await screen.findByLabelText('EMA Trend instrument search');
  fireEvent.change(search, { target: { value: 'NIFTY' } });
  const option = await screen.findByRole('checkbox', { name: 'NFO:NIFTY26NOV25000CE' });
  fireEvent.click(option);
  expect(api.patch).not.toHaveBeenCalled();
  expect(screen.getByRole('checkbox', { name: 'MCX:GOLD26NOVFUT' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/api/v1/ema-trend/config', {
    expected_revision: 7, changes: { symbols: [...emaConfig().config.symbols, 'NFO:NIFTY26NOV25000CE'] },
  }));
});


it('selects the curated F&O stock universe and preserves stocks when disabled', async () => {
  vi.mocked(api.get).mockImplementation(async path =>
    String(path).includes('/stock-registry')
      ? [{ liquidity: 'Very High', stocks: [
        { name: 'RELIANCE', label: 'Reliance' },
        { name: 'HDFCBANK', label: 'HDFC Bank' },
      ] }] as never
      : response as never);
  show();
  const all = await screen.findByRole('switch', { name: 'EMA scan all eligible F&O stocks' });
  await waitFor(() => expect(all).toBeEnabled());
  fireEvent.click(all);
  fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
  await waitFor(() => expect(response.config.symbols).toEqual([
    'NSE:TCS', 'NSE:RELIANCE', 'NSE:HDFCBANK',
  ]));
  const stockSwitch = screen.getByRole('switch', { name: 'EMA scan single-stock underlyings' });
  fireEvent.click(stockSwitch);
  fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
  await waitFor(() => expect(response.config.scan_stocks_enabled).toBe(false));
  expect(response.config.symbols).toContain('NSE:RELIANCE');
});

it('allows a cash stock outside curated F&O registry only after exact broker lookup', async () => {
  vi.mocked(api.get).mockImplementation(async path => {
    if (String(path).includes('/stock-registry')) return [] as never;
    if (String(path).includes('/instruments?')) {
      return { items: [{
        symbol: 'NSE:VOLTAS', name: 'Voltas', exchange: 'NSE',
        instrument_type: 'EQ', expiry: '', lot_size: 1, tick_size: '0.05',
        execution_supported: true, blocker: null, contract: null,
      }] } as never;
    }
    return response as never;
  });
  show();
  fireEvent.click(await screen.findByText(/Add more stocks or exact derivative contracts/));
  const input = await screen.findByLabelText('EMA Trend instrument search');
  fireEvent.change(input, { target: { value: 'VOLTAS' } });
  fireEvent.click(await screen.findByRole('checkbox', { name: 'NSE:VOLTAS' }));
  expect(api.patch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
  await waitFor(() => expect(response.config.symbols).toContain('NSE:VOLTAS'));
});

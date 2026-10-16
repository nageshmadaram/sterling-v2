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
    const input = await screen.findByLabelText('EMA Trend equities');
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
  it('stages server defaults without changing the running backend until Apply', async () => {
    show();
    await screen.findByLabelText('EMA Trend equities');
    fireEvent.click(screen.getByRole('button', { name: 'Stage server defaults' }));
    expect(screen.getByLabelText('Paper allocation (₹)')).toHaveValue(0);
    expect(screen.getByText('RUNNING')).toBeInTheDocument();
    expect(screen.getByText('WILL STOP ON APPLY')).toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

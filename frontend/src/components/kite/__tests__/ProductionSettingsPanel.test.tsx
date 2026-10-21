import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProductionSettingsPanel, buildRiskLimits, parseSimpleLimits } from '../ProductionSettingsPanel';

const { get, put, post } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock('../../../utils/api', () => ({ api: { get, put, post } }));
vi.mock('../config/unsavedDraftGuard', () => ({ useUnsavedDraftGuard: vi.fn() }));
const snapshot = {
  settings: { allocation_capital_inr: null, experiment_id: '', dataset_start: '', risk_limits: '', live_execution: false },
  runtime: { enabled: false }, safe_mode: { state: 'NORMAL', reason: '' },
  risk_scopes: { strategies: ['gamma_move', 'snapback', 'supertrend'], lanes: ['snapback:swing', 'supertrend:intraday'] },
  posture: { ready: false, missing: ['static IP not verified'], egress: { registered_ip: '1.1.1.1', observed_ip: '2.2.2.2', reason: 'IP mismatch' }, binding: { binding_id: 'account-1', detail: 'Verification expired' } },
};
function show() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProductionSettingsPanel /></QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); get.mockResolvedValue(snapshot); put.mockResolvedValue({ restart_required: true }); post.mockResolvedValue({}); });

describe('production settings', () => {
  it('saves capital with entries disabled and explains activation', async () => {
    show();
    fireEvent.change(await screen.findByLabelText('Approved live capital (₹)'), { target: { value: '50000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/ops/production-settings', expect.objectContaining({ settings: expect.objectContaining({ allocation_capital_inr: 50000, live_execution: false }) })));
    expect(await screen.findByText(/Production settings saved/)).toHaveTextContent('Restart the backend');
  });

  it('does not offer to save live activation while posture is blocked', async () => {
    show();
    fireEvent.click(await screen.findByLabelText('Request live entries after restart'));
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled();
    expect(screen.getByText(/Live setup requires/)).toHaveTextContent('static IP not verified');
    expect(put).not.toHaveBeenCalled();
  });

  it('requires the whitelist acknowledgement before accepting the observed IP', async () => {
    show();
    const button = await screen.findByRole('button', { name: 'Verify and accept observed IP' });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/I have added the observed IP/));
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/ops/live-posture/egress/accept', { ip: '2.2.2.2', confirm: 'ACCEPT IP' }));
  });

  it('shows failure without presenting editable defaults', async () => {
    get.mockRejectedValue(new Error('Forbidden'));
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent('Administrator access is required');
    expect(screen.queryByLabelText('Approved live capital (₹)')).not.toBeInTheDocument();
  });

  it('builds every lane cap from four plain limits', async () => {
    show();
    for (const [label, value] of [['Total limit (₹)', '100000'], ['Per strategy (₹)', '50000'], ['Per mode (₹)', '20000'], ['Per trade (₹)', '5000']]) {
      fireEvent.change(await screen.findByLabelText(label), { target: { value } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    const sent = JSON.parse(put.mock.calls[0][1].settings.risk_limits);
    expect(sent).toEqual({
      global: { '': 100000 },
      strategy: { gamma_move: 50000, snapback: 50000, supertrend: 50000 },
      mode: { 'snapback:swing': 20000, 'supertrend:intraday': 20000 },
      position: { 'snapback:swing': 5000, 'supertrend:intraday': 5000 },
    });
  });

  it('blocks save until all four limits are filled and nested', async () => {
    show();
    fireEvent.change(await screen.findByLabelText('Total limit (₹)'), { target: { value: '1000' } });
    expect(screen.getByText('Fill all four limits.')).toHaveAttribute('role', 'alert');
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Per strategy (₹)'), { target: { value: '5000' } });
    fireEvent.change(screen.getByLabelText('Per mode (₹)'), { target: { value: '500' } });
    fireEvent.change(screen.getByLabelText('Per trade (₹)'), { target: { value: '100' } });
    expect(screen.getByText(/at most the one above it/)).toHaveAttribute('role', 'alert');
    expect(screen.getByRole('button', { name: 'Apply changes' })).toBeDisabled();
  });

  it('round-trips saved limits into the four fields', () => {
    const raw = buildRiskLimits({ total: 9, strategy: 7, lane: 5, trade: 3 }, { strategies: ['snapback'], lanes: ['snapback:swing'] });
    expect(parseSimpleLimits(raw)).toEqual({ total: 9, strategy: 7, lane: 5, trade: 3 });
    expect(buildRiskLimits({ total: 9, strategy: null, lane: 5, trade: 3 }, { strategies: ['snapback'], lanes: ['snapback:swing'] })).toBe('');
  });
});

describe('live entry blocker', () => {
  it('says why Apply is disabled and offers a broker re-verify', async () => {
    show();
    fireEvent.click(await screen.findByLabelText('Request live entries after restart'));
    fireEvent.change(screen.getByLabelText('Type ENABLE LIVE ENTRIES'), { target: { value: 'ENABLE LIVE ENTRIES' } });
    expect(screen.getByText(/Apply is disabled:/)).toHaveTextContent('Set approved live capital');
    fireEvent.click(screen.getByRole('button', { name: 'Re-verify broker account' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/ops/live-posture/binding/verify', {}));
  });
});

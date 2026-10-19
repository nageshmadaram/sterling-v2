import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProductionSettingsPanel } from '../ProductionSettingsPanel';

const { get, put, post } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock('../../../utils/api', () => ({ api: { get, put, post } }));
vi.mock('../config/unsavedDraftGuard', () => ({ useUnsavedDraftGuard: vi.fn() }));
const snapshot = {
  settings: { allocation_capital_inr: null, experiment_id: '', dataset_start: '', risk_limits: '', live_execution: false },
  runtime: { enabled: false }, safe_mode: { state: 'NORMAL', reason: '' },
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
    fireEvent.click(screen.getByRole('button', { name: 'Save production settings' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/ops/production-settings', expect.objectContaining({ settings: expect.objectContaining({ allocation_capital_inr: 50000, live_execution: false }) })));
    expect(await screen.findByText(/Production settings saved/)).toHaveTextContent('Restart the backend');
  });

  it('does not offer to save live activation while posture is blocked', async () => {
    show();
    fireEvent.click(await screen.findByLabelText('Request live entries after restart'));
    expect(screen.getByRole('button', { name: 'Save production settings' })).toBeDisabled();
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
});

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmProvider } from '../../components/kite/ConfirmDialog';
import { useExecuteSnapbackSignal } from '../useSnapback';
import { api } from '../../utils/api';
vi.mock('../../utils/api', () => ({ api: { post: vi.fn(), get: vi.fn() } }));
vi.mock('../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));
const plan = { plan_id: 'p1', revision: 2, status: 'PUBLISHED', mode: 'LIVE', option_symbol: 'TESTCE',
  option_quantity: 50, limit_price: 100, stop_premium: 80, target_premium: null,
  futures_symbol: 'TESTFUT', target_futures_quantity: 50, hedge_side: 'SELL', hedge_price_limit: 1000,
  cash_required: 5000, margin_required: 12000, risk_amount: 1000, expires_at: '2099-10-23T10:00:00+05:30' };
function Entry() {
  const action = useExecuteSnapbackSignal();
  return <button onClick={() => action.mutate('signal-1')}>Review entry</button>;
}
function mount() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
    <ConfirmProvider><Entry /></ConfirmProvider>
  </QueryClientProvider>);
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.post).mockReset(); vi.mocked(api.get).mockReset();
  vi.mocked(api.get).mockResolvedValue(plan);
  vi.mocked(api.post).mockResolvedValueOnce({ ok: true, plan_id: 'p1', revision: 2, status: 'PUBLISHED' })
    .mockResolvedValueOnce({ plan_id: 'p1', revision: 3, status: 'ARMED', mode: 'LIVE', idempotent_replay: false });
});
describe('Snapback plan review screen', () => {
  it('holds arming until the exact server plan has been reviewed', async () => {
    mount(); fireEvent.click(screen.getByRole('button', { name: 'Review entry' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Review Snapback real entry' });
    expect(dialog).toHaveTextContent('Hedge margin: ₹12,000.00');
    expect(dialog).toHaveTextContent('Stop: ₹80.00');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Leave unarmed' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Arm this revision' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/snapback/plans/p1/arm', {
      expected_revision: 2, idempotency_key: 'snapback-ui:p1:2', mode: 'LIVE',
    }));
  });
  it('Escape dismisses the review without an arm or cancellation request', async () => {
    mount(); fireEvent.click(screen.getByRole('button', { name: 'Review entry' }));
    await screen.findByRole('alertdialog');
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

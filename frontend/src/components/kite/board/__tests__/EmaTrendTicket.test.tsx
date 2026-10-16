import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EmaTrendTicket } from '../EmaTrendTicket';
import { BoardDetailPane } from '../BoardDetailPane';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from '../emaTrendAdapter';
import { emaCampaign, emaSetup } from '../../../../test/emaTrendFixtures';
import { api } from '../../../../utils/api';
const quote = vi.hoisted(() => vi.fn(() => ({ data: {} })));
const calculator = vi.hoisted(() => vi.fn(() => <div>Generic option calculator</div>));
vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: quote }));
vi.mock('../../AdaptiveEdgePositionCalculator', () => ({ AdaptiveEdgePositionCalculator: calculator }));
vi.mock('../../../../utils/api', () => ({ api: { post: vi.fn(), get: vi.fn() } }));
function show(content: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{content}</QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.get).mockResolvedValue({ campaign: emaCampaign(), events: [] }); });
afterEach(cleanup);
describe('EMA Trend equity actions and full detail guards', () => {
  it('requests a revision-bound reduction and never sends an editable share quantity', async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 'ACKNOWLEDGED' });
    show(<EmaTrendTicket signal={emaTrendCampaignsToBoard([emaCampaign()])[0]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reduce 25% paper' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/ema-trend/campaigns/campaign-swing/actions', {
      action: 'reduce', percent: 25, expected_revision: 4, idempotency_key: 'ema-trend-ui:campaign-swing:4:reduce:25',
    }));
    expect(calculator).not.toHaveBeenCalled();
  });
  it('blocks ordinary reduction but allows a full close while a sell is pending', async () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.post).mockResolvedValue({ status: 'UNRESOLVED' });
    const signal = emaTrendCampaignsToBoard([{ ...emaCampaign(), pending: { id: 'a', kind: 'WEAK_10', side: 'SELL', quantity: 25, filled_quantity: 10, status: 'PARTIAL', limit: '100', at_ms: Date.now() } }])[0];
    show(<EmaTrendTicket signal={signal} />);
    expect(screen.getByRole('button', { name: 'Reduce 25% paper' })).toBeDisabled();
    const close = screen.getByRole('button', { name: 'Close paper campaign' });
    expect(close).toBeEnabled();
    fireEvent.click(close);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/ema-trend/campaigns/campaign-swing/actions', {
      action: 'close', percent: undefined, expected_revision: 4, idempotency_key: 'ema-trend-ui:campaign-swing:4:close:100',
    }));
    expect(await screen.findByText(/Close requested; order reconciliation is still pending/)).toBeInTheDocument();
  });
  it('can request closure when an entry is pending with zero confirmed shares', () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    const queued = { ...emaCampaign(), state: 'ENTRY_PENDING', qty: 0, lots: [],
      pending: { id: 'entry', kind: 'ENTRY', side: 'BUY' as const, quantity: 4, filled_quantity: 0,
        status: 'ACKNOWLEDGED', limit: '100', at_ms: Date.now() } };
    show(<EmaTrendTicket signal={emaTrendCampaignsToBoard([queued])[0]} />);
    expect(screen.getByRole('button', { name: 'Reduce 25% paper' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Close paper campaign' })).toBeEnabled();
  });
  it('retains full close control when status is CLOSED but an entry order remains disputed', () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    const pending = { id: 'entry-unknown', kind: 'ENTRY', side: 'BUY' as const,
      quantity: 4, filled_quantity: 0, status: 'UNKNOWN', limit: '100', at_ms: Date.now() };
    const [signal] = emaTrendCampaignsToBoard([{ ...emaCampaign(), state: 'CLOSED',
      pending, qty: 0, lots: [], flatten_requested: false }]);
    show(<EmaTrendTicket signal={signal} />);
    expect(screen.getByRole('button', { name: 'Close paper campaign' })).toBeEnabled();
  });
  it('full detail is equity-aware, uses no generic order calculator and polls no live quote', () => {
    show(<BoardDetailPane signal={emaTrendSetupsToBoard([emaSetup()])[0]} onClose={() => {}} />);
    expect(screen.getByText('PAPER · CASH EQUITY')).toBeInTheDocument();
    expect(screen.getByText('EQUITY · LONG')).toBeInTheDocument();
    expect(calculator).not.toHaveBeenCalled();
    expect(quote).toHaveBeenCalledWith([], false, 5000, 'full');
  });
  it('historical full detail keeps all strategy execution disabled and never calls a mutation', () => {
    show(<BoardDetailPane signal={emaTrendSetupsToBoard([emaSetup()], true)[0]} onClose={() => {}} />);
    const execute = screen.getByRole('button', { name: 'Execute paper plan' });
    expect(execute).toBeDisabled(); fireEvent.click(execute);
    expect(api.post).not.toHaveBeenCalled();
    expect(quote).toHaveBeenCalledWith([], false, 5000, 'full');
  });
});

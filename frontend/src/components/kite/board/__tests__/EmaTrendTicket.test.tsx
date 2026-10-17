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
  it('exports the server evidence chain without submitting an order', async () => {
    vi.mocked(api.get).mockImplementation(async (path) => String(path).endsWith('/evidence')
      ? { status: 'INCOMPLETE', actions: [{ confirmed_quantity: 0 }], gaps: ['DATASET_LINK_MISSING'] }
      : { campaign: emaCampaign(), events: [] });
    const create = vi.fn(() => 'blob:evidence');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    show(<EmaTrendTicket signal={emaTrendCampaignsToBoard([emaCampaign()])[0]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Export execution evidence' }));
    await screen.findByText(/Evidence: INCOMPLETE/);
    expect(api.get).toHaveBeenCalledWith('/api/v1/ema-trend/campaigns/campaign-swing/evidence');
    expect(screen.getByText(/DATASET_LINK_MISSING/)).toBeInTheDocument();
    expect(create).toHaveBeenCalled(); expect(click).toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    click.mockRestore();
  });
  it('requests a revision-bound reduction and never sends an editable share quantity', async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 'ACKNOWLEDGED' });
    show(<EmaTrendTicket signal={emaTrendCampaignsToBoard([emaCampaign()])[0]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reduce 25% paper' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/ema-trend/campaigns/campaign-swing/actions', {
      action: 'reduce', percent: 25, expected_revision: 4, idempotency_key: 'ema-trend-ui:campaign-swing:4:reduce:25',
    }));
    expect(calculator).not.toHaveBeenCalled();
  });
  it('does not enable new actions while an owned action is pending', () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    const signal = emaTrendCampaignsToBoard([{ ...emaCampaign(), pending: { id: 'a', kind: 'WEAK_10', side: 'SELL', quantity: 25, filled_quantity: 10, status: 'PARTIAL', limit: '100', at_ms: Date.now() } }])[0];
    show(<EmaTrendTicket signal={signal} />);
    expect(screen.getByRole('button', { name: 'Reduce 25% paper' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Close paper campaign' })).toBeDisabled();
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

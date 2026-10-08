import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { OrdersPane } from '../OrdersPane';

const h = vi.hoisted(() => ({
  cancelMutate: vi.fn(),
  sipData: [] as any[],
}));

vi.mock('../../../hooks/useKite', () => ({
  useKiteOrders: () => ({ data: [] }),
  useCancelKiteOrder: () => ({ isPending: false, mutate: vi.fn() }),
  useKiteMfSips: () => ({ data: h.sipData, isLoading: false, error: null }),
  useCancelKiteMfSip: () => ({ mutate: h.cancelMutate, isPending: false, error: null }),
}));

vi.mock('../../../store/useKiteBasketStore', () => ({
  useKiteBasketStore: (selector: any) => selector({ entries: [] }),
}));

vi.mock('../GttPane', () => ({ GttPane: () => <div>GTT pane</div> }));
vi.mock('../AlertsPane', () => ({ AlertsPane: () => <div>Alerts pane</div> }));
vi.mock('../InstrumentLabel', () => ({ InstrumentLabel: ({ symbol }: any) => <span>{symbol}</span> }));
vi.mock('../ModifyOrderModal', () => ({ ModifyOrderModal: () => null }));
vi.mock('../OrderHistoryRow', () => ({ OrderHistoryRow: () => null }));

const SIP = {
  sip_id: 'SIP-1',
  tradingsymbol: 'INF846K01EW2',
  fund: 'Axis Bluechip Fund',
  amount: 1000,
  frequency: 'monthly',
  instalments: 12,
  status: 'active',
};

beforeEach(() => {
  h.cancelMutate.mockClear();
  h.sipData = [];
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('OrdersPane SIP', () => {
  it('opens the real basket from an empty orders page', () => {
    const openBasket = vi.fn();
    render(<OrdersPane onOpenBasket={openBasket} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open basket' }));

    expect(openBasket).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('View history')).toBeNull();
    expect(screen.queryByText('Get started')).toBeNull();
  });

  it('never offers SIP creation or pause, and says nothing about simulation', () => {
    h.sipData = [SIP];
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));

    expect(screen.queryByRole('button', { name: /New SIP|Create SIP/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Pause|Resume/ })).toBeNull();
    expect(screen.queryByText(/simulat|paper/i)).toBeNull();
    expect(screen.getByText(/View and cancel SIPs/)).toBeInTheDocument();
  });

  it('shows the amount as rupees', () => {
    h.sipData = [SIP];
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));
    expect(screen.getByText('₹1,000')).toBeInTheDocument();
  });

  it('cancels an existing SIP after confirmation', () => {
    h.sipData = [SIP];
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(window.confirm).toHaveBeenCalled();
    expect(h.cancelMutate).toHaveBeenCalledWith('SIP-1');
  });

  it('does not cancel when the confirmation is declined', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    h.sipData = [SIP];
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(h.cancelMutate).not.toHaveBeenCalled();
  });

  it('says so when there are no SIPs', () => {
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));
    expect(screen.getByText('No SIPs.')).toBeInTheDocument();
  });
});

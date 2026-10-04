import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { OrdersPane } from '../OrdersPane';

const h = vi.hoisted(() => ({
  createMutate: vi.fn(),
  modifyMutate: vi.fn(),
  cancelMutate: vi.fn(),
  sipData: [] as any[],
  statusData: { connected: true, is_paper: true, message: 'Paper' } as any,
  searchData: {
    query: 'axis',
    count: 1,
    instruments: [{
      tradingsymbol: 'INF846K01EW2',
      name: 'Axis Bluechip Fund',
      amc: 'Axis Mutual Fund',
      minimum_purchase_amount: 500,
    }],
  },
}));

vi.mock('../../../hooks/useKite', () => ({
  useKiteOrders: () => ({ data: [] }),
  useCancelKiteOrder: () => ({ isPending: false, mutate: vi.fn() }),
  useKiteStatus: () => ({ data: h.statusData }),
  useKiteMfSips: () => ({ data: h.sipData, isLoading: false, error: null }),
  useKiteMfInstrumentSearch: () => ({ data: h.searchData, isLoading: false }),
  usePlaceKiteMfSip: () => ({ mutate: h.createMutate, isPending: false, error: null }),
  useModifyKiteMfSip: () => ({ mutate: h.modifyMutate, isPending: false, error: null }),
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

beforeEach(() => {
  h.createMutate.mockClear();
  h.modifyMutate.mockClear();
  h.cancelMutate.mockClear();
  h.sipData = [];
  h.statusData = { connected: true, is_paper: true, message: 'Paper' };
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

  it('creates a SIP from the real scheme search result', () => {
    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));
    fireEvent.click(screen.getByRole('button', { name: 'New SIP' }));
    fireEvent.change(screen.getByLabelText('Search mutual funds'), { target: { value: 'axis' } });
    fireEvent.click(screen.getByRole('button', { name: /Axis Bluechip Fund/i }));
    fireEvent.change(screen.getByLabelText('SIP amount'), { target: { value: '1500' } });
    fireEvent.change(screen.getByLabelText('SIP instalments'), { target: { value: '24' } });
    fireEvent.change(screen.getByLabelText('SIP frequency'), { target: { value: 'monthly' } });

    fireEvent.click(screen.getByRole('button', { name: 'Create SIP' }));

    expect(h.createMutate).toHaveBeenCalledWith(
      {
        tradingsymbol: 'INF846K01EW2',
        amount: 1500,
        instalments: 24,
        frequency: 'monthly',
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it('live mode hides unsupported SIP creation and pause but keeps cancellation', () => {
    h.statusData = { connected: true, is_paper: false, message: 'Live' };
    h.sipData = [{
      sip_id: 'SIP-LIVE-1',
      tradingsymbol: 'INF846K01EW2',
      fund: 'Axis Bluechip Fund',
      amount: 1000,
      frequency: 'monthly',
      instalments: 12,
      status: 'active',
    }];

    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));

    expect(screen.queryByRole('button', { name: 'New SIP' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    expect(screen.getByText(/Live SIP creation and pause\/resume are disabled/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(h.cancelMutate).toHaveBeenCalledWith('SIP-LIVE-1');
    expect(h.modifyMutate).not.toHaveBeenCalled();
    expect(h.createMutate).not.toHaveBeenCalled();
  });

  it('pauses and cancels an existing SIP', () => {
    h.sipData = [{
      sip_id: 'SIP-1',
      tradingsymbol: 'INF846K01EW2',
      fund: 'Axis Bluechip Fund',
      amount: 1000,
      frequency: 'monthly',
      instalments: 12,
      status: 'active',
    }];

    render(<OrdersPane />);
    fireEvent.click(screen.getByText('SIP'));

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(h.modifyMutate).toHaveBeenCalledWith({ id: 'SIP-1', status: 'paused' });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(h.cancelMutate).toHaveBeenCalledWith('SIP-1');
  });
});

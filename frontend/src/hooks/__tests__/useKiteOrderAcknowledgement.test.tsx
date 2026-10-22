import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const mocks = vi.hoisted(() => ({ post: vi.fn(), notify: vi.fn() }));
vi.mock('../../utils/api', () => ({ api: { post: mocks.post } }));
vi.mock('../../store/useKiteNotifications', () => ({ notifyOrder: mocks.notify }));
import { usePlaceKiteOrder } from '../useKite';
const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
beforeEach(() => { mocks.post.mockReset(); mocks.notify.mockReset(); });
it('reports canonical acknowledgement as fill pending without false protection failure', async () => {
  mocks.post.mockResolvedValue({ order_id: 'one', execution_state: 'ACKNOWLEDGED', protected: false,
    protection_owner: 'engine', protection: 'Engine protection will be verified after confirmed fill', variety: 'regular' });
  const { result } = renderHook(() => usePlaceKiteOrder(), { wrapper });
  await act(async () => { await result.current.mutateAsync({ tradingsymbol: 'TESTCE', exchange: 'NFO', transaction_type: 'BUY', quantity: 50, order_type: 'LIMIT', price: 100, product: 'NRML' }); });
  expect(mocks.notify).toHaveBeenCalledWith(expect.objectContaining({ title: 'Entry acknowledged — fill pending' }));
  expect(JSON.stringify(mocks.notify.mock.calls)).not.toMatch(/UNPROTECTED|No automatic exit|Protected:/);
});

it('does not mislabel an uncertain submission as a rejection', async () => {
  mocks.post.mockRejectedValue(new Error('Entry outcome uncertain; broker reconciliation required'));
  const { result } = renderHook(() => usePlaceKiteOrder(), { wrapper });
  await act(async () => { await result.current.mutateAsync({ tradingsymbol: 'TESTCE', exchange: 'NFO', transaction_type: 'BUY', quantity: 50, order_type: 'LIMIT', price: 100, product: 'NRML' }).catch(() => {}); });
  expect(mocks.notify).toHaveBeenCalledWith(expect.objectContaining({ title: 'Order outcome unknown — reconcile before retrying', kind: 'info' }));
});

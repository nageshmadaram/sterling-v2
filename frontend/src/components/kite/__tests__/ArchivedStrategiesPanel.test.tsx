import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ArchivedStrategiesPanel } from '../ArchivedStrategiesPanel';
import { api } from '../../../utils/api';
let simulation = false;
vi.mock('../../../store/useViewStore', () => ({ useIsSimulation: () => simulation }));
vi.mock('../../../utils/api', () => ({ api: { get: vi.fn(), put: vi.fn() } }));
const row = { id: 'intraday', name: 'Intraday Pack', description: 'Pivot Break, MA Ribbon, VWAP SuperTrend', enabled: false };
function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><ArchivedStrategiesPanel /></QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); simulation = false; vi.mocked(api.get).mockResolvedValue({ strategies: [row] }); });
describe('Archived settings', () => {
  it('loads server state and persists enable, then disable', async () => {
    vi.mocked(api.put).mockImplementation(async (_url, body: any) => ({ strategies: [{ ...row, enabled: body.enabled }] }) as any);
    mount();
    const toggle = await screen.findByRole('checkbox', { name: 'Enable Intraday Pack' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(api.put).toHaveBeenLastCalledWith('/api/v1/config/archived-strategies/intraday', { enabled: true });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(api.put).toHaveBeenLastCalledWith('/api/v1/config/archived-strategies/intraday', { enabled: false });
  });
  it('shows failed save without claiming enabled', async () => {
    vi.mocked(api.put).mockRejectedValue(new Error('Storage unavailable'));
    mount();
    const toggle = await screen.findByRole('checkbox');
    fireEvent.click(toggle);
    expect(await screen.findByRole('alert')).toHaveTextContent('Storage unavailable');
    expect(toggle).not.toBeChecked();
  });
  it('does no live reads or writes in Simulation', () => {
    simulation = true; mount();
    expect(screen.getByText(/available in Live/)).toBeInTheDocument();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  });
});

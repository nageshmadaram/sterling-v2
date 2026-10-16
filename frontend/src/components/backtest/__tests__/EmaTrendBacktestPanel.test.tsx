import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EmaTrendBacktestPanel } from '../EmaTrendBacktestPanel';
import { api } from '../../../utils/api';
import { emaReport } from '../../../test/emaTrendFixtures';
vi.mock('../../../utils/api', () => ({ api: { post: vi.fn() } }));
beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);
describe('EMA Trend native unified backtest', () => {
  it('sends an explicit spot-equity/profile request and presents campaigns and fill legs', async () => {
    vi.mocked(api.post).mockResolvedValue({ format: 'EMA_TREND_LIFECYCLE_V1', strategy: 'ema_trend', report: emaReport('scalping') });
    render(<QueryClientProvider client={new QueryClient()}><EmaTrendBacktestPanel onBack={() => {}} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText('Backtest cash equity'), { target: { value: 'NSE:TCS' } });
    fireEvent.change(screen.getByLabelText('Backtest EMA Trend profile'), { target: { value: 'scalping' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run EMA Trend backtest' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/backtest/unified/run', expect.objectContaining({
      strategy: 'ema_trend', symbol: 'NSE:TCS', contract_type: 'spot', lot_size: 1, timeframe: '3m', strategy_params: { mode: 'scalping' },
    })));
    expect(await screen.findByText('Open at dataset end')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Fill legs · read-only' })).toBeInTheDocument();
    expect(screen.getByText('INCONCLUSIVE')).toBeInTheDocument();
    expect(screen.queryByText('Win rate')).not.toBeInTheDocument();
  });
});

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EnginePositionsPane } from '../EnginePositionsPane';

const position = {
  symbol: 'NIFTY26OCT24000CE', exchange: 'NFO', token: 9001, qty: 150, lot_size: 75,
  entry_premium: 100, fill_price: 101, stop_premium: 118, initial_stop_premium: 90,
  status: 'open', direction: 'long', vehicle: 'otm_options', underlying: 'NIFTY 50',
  opened_ms: Date.now() - 3_600_000, exit_reason: '', order_id: 'OID1', exit_mode: 'one_red',
  current_red_count: 0, exit_threshold: 1,
};

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineOpenPositions: () => ({ data: { positions: [position] }, isLoading: false }),
  useEngineSignals: () => ({ data: { rows: [] } }),
  useCloseEnginePosition: () => ({ mutate: vi.fn(), isError: false }),
}));
vi.mock('../../../hooks/useKite', () => ({
  useKiteQuote: () => ({ data: { 'NFO:NIFTY26OCT24000CE': { last_price: 121 } } }),
}));

describe('EnginePositionsPane', () => {
  it('shows the canonical columns with P&L in rupees on units', () => {
    render(<EnginePositionsPane />);
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent);
    for (const label of ['Entry', 'LTP', 'SL / TSL', 'P&L', 'Risk', 'Capital', 'Qty']) {
      expect(heads).toContain(label);
    }
    const row = screen.getByText('NIFTY26OCT24000CE').closest('tr')!;
    // (121 - 101 fill) x 150 units; the lot size is never applied twice.
    expect(within(row).getByText(/\+₹3,000/)).toBeTruthy();
    // Risk is from the ENTRY stop: (101 - 90) x 150. Capital: 101 x 150.
    expect(within(row).getByText('₹1,650')).toBeTruthy();
    expect(within(row).getByText('₹15,150')).toBeTruthy();
    // The stop has trailed past its entry level: it is a TSL, not an SL.
    expect(within(row).getByText('TSL')).toBeTruthy();
  });

  it('colours slippage as a cost when the fill was above the intended entry', () => {
    render(<EnginePositionsPane />);
    const row = screen.getByText('NIFTY26OCT24000CE').closest('tr')!;
    const slip = within(row).getByText(/\(\+1\.0%\)/);
    expect(slip.getAttribute('style')).toContain('var(--k-red)');
  });
});

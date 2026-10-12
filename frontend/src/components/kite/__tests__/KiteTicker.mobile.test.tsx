import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { KiteTicker } from '../KiteTicker';
import { useTickerPins } from '../../../store/useTickerPins';

vi.mock('../../../hooks/useKite', () => ({
  useKiteQuote: () => ({ data: { 'NSE:NIFTY 50': { last_price: 24200, ohlc: { close: 24100 } } } }),
}));

vi.mock('../../../hooks/useCandles', () => ({
  useCandles: () => ({ data: [] }),
}));

const renderWithClient = (ui: React.ReactElement) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

describe('KiteTicker mobile search behavior', () => {
  const originalWidth = window.innerWidth;

  beforeEach(() => {
    cleanup();
    localStorage.clear();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    useTickerPins.setState({
      pins: ['NSE:NIFTY 50'],
      stripEnabled: true,
      tileScale: 1,
      tileStyle: 'card',
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
    cleanup();
  });

  it('does not display any swipe-to-search indicator', () => {
    const { container } = renderWithClient(<KiteTicker compact />);
    expect(container.querySelector('.ticker-search-pull')).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/swipe to search/i);
    expect(container.textContent).not.toMatch(/pull to search/i);
    expect(container.textContent).not.toMatch(/release to search/i);
  });

  it('does not dispatch search event when swiped horizontally on mobile', () => {
    const searchListener = vi.fn();
    window.addEventListener('kite-open-watchlist-search', searchListener);

    try {
      const { container } = renderWithClient(<KiteTicker compact />);
      const strip = container.querySelector('.kite-ticker-strip') || container.firstElementChild;
      expect(strip).toBeTruthy();

      const touch = (x: number, y: number) => ({
        touches: [{ clientX: x, clientY: y }],
      });

      // Horizontal swipe gestures
      fireEvent.touchStart(strip!, touch(50, 20));
      fireEvent.touchMove(strip!, touch(200, 20));
      fireEvent.touchEnd(strip!);

      fireEvent.touchStart(strip!, touch(200, 20));
      fireEvent.touchMove(strip!, touch(50, 20));
      fireEvent.touchEnd(strip!);

      expect(searchListener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('kite-open-watchlist-search', searchListener);
    }
  });

  it('does not render swipe-to-search even when no pins exist', () => {
    useTickerPins.setState({ pins: [] });
    const { container } = renderWithClient(<KiteTicker compact />);
    expect(container.querySelector('.ticker-search-pull')).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/swipe to search/i);
  });
});

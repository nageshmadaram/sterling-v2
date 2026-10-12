import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TickerStripSettings } from '../TickerStripSettings';
import { useExperienceOptions, readExperienceOptions } from '../../../../store/useExperienceOptions';
import { useTickerPins } from '../../../../store/useTickerPins';

const renderWithClient = (ui: React.ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
    </QueryClientProvider>
  );
};

describe('TickerStripSettings PCR preview integration', () => {
  beforeEach(() => {
    localStorage.clear();
    useExperienceOptions.setState({ options: readExperienceOptions() });
    useTickerPins.setState({
      stripEnabled: true,
      tileStyle: 'card',
      tileScale: 1,
      pins: ['NSE:NIFTY 50'],
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('initially hides PCR line when content is default graph', () => {
    const { container } = renderWithClient(<TickerStripSettings />);
    expect(container.querySelector('.ticker-pcr-line')).not.toBeInTheDocument();
  });

  it('shows PCR info in preview cards when tile content is changed to "both"', () => {
    const { container } = renderWithClient(<TickerStripSettings />);
    const select = screen.getByRole('combobox', { name: 'Ticker content' });
    fireEvent.change(select, { target: { value: 'both' } });

    // The PCR lines should now be present in the previews
    const pcrLines = container.querySelectorAll('.ticker-pcr-line');
    expect(pcrLines.length).toBeGreaterThan(0);
    expect(screen.getAllByText(/PCR 1\.18/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Buy CE/).length).toBeGreaterThan(0);
  });

  it('shows detailed PCR insights when tile content is set to "detail"', () => {
    const { container } = renderWithClient(<TickerStripSettings />);
    const select = screen.getByRole('combobox', { name: 'Ticker content' });
    fireEvent.change(select, { target: { value: 'detail' } });

    const pcrLines = container.querySelectorAll('.ticker-pcr-line');
    expect(pcrLines.length).toBeGreaterThan(0);
    expect(screen.getAllByText(/conviction/).length).toBeGreaterThan(0);
  });

  it('updates metric ratio when changing PCR metric to volume or change in OI', () => {
    renderWithClient(<TickerStripSettings />);
    const contentSelect = screen.getByRole('combobox', { name: 'Ticker content' });
    fireEvent.change(contentSelect, { target: { value: 'pcr' } });

    // Metric change to volume
    const metricSelect = screen.getByRole('combobox', { name: 'Ticker PCR metric' });
    fireEvent.change(metricSelect, { target: { value: 'volume' } });
    expect(screen.getAllByText(/PCR 0\.96/).length).toBeGreaterThan(0);

    // Metric change to changeOi
    fireEvent.change(metricSelect, { target: { value: 'changeOi' } });
    expect(screen.getAllByText(/PCR 1\.32/).length).toBeGreaterThan(0);
  });

  it('applies mobile layout class to preview cards when desktop ticker layout is switched to mobile', () => {
    const { container } = renderWithClient(<TickerStripSettings />);
    const layoutSelect = screen.getByRole('combobox', { name: 'Desktop ticker layout' });
    fireEvent.change(layoutSelect, { target: { value: 'mobile' } });

    const mobileEnhancedCards = container.querySelectorAll('.ticker-layout-mobile');
    expect(mobileEnhancedCards.length).toBeGreaterThan(0);
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), put: vi.fn(), post: vi.fn() } }));
vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));

import { SimBanner } from '../SimBanner';
import { useViewStore } from '../../../store/useViewStore';
import { apiError, makeStatus, renderWithClient, strategies } from './simTestUtils';

beforeEach(() => { get.mockReset(); useViewStore.setState({ view: 'live' }); });
afterEach(() => { cleanup(); useViewStore.setState({ view: 'live' }); });

describe('SimBanner in the Simulation view', () => {
  beforeEach(() => { useViewStore.setState({ view: 'simulation' }); });

  it('says in words that the money is virtual and no real order is sent', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<SimBanner />);
    const banner = screen.getByRole('region', { name: 'Simulation' });
    expect(banner).toHaveTextContent('SIMULATION — virtual money, no real orders');
    expect(banner).toHaveTextContent(/real Zerodha account is not touched/);
    await screen.findByText('₹9,00,000.00');
  });

  it('shows virtual cash, equity and today\'s P&L from GET /sim/status, in the shared formats', async () => {
    get.mockResolvedValue(makeStatus({ cash: 812_345.5, equity: 1_010_000, today_pnl: -750 }));
    renderWithClient(<SimBanner />);
    await screen.findByText('₹8,12,345.50');
    expect(get).toHaveBeenCalledWith('/api/v1/sim/status');
    expect(screen.getByText('₹10,10,000.00')).toBeInTheDocument();
    expect(screen.getByText('-₹750.00')).toBeInTheDocument();
    expect(screen.getByText('Virtual cash')).toBeInTheDocument();
    expect(screen.getByText('Equity')).toBeInTheDocument();
    expect(screen.getByText("Today's P&L")).toBeInTheDocument();
  });

  it('is amber, not the live green, so the two views cannot be mistaken', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<SimBanner />);
    const banner = screen.getByTestId('sim-banner');
    expect(banner.style.borderBottom).toContain('var(--k-amber-2)');
    expect(banner.style.background).toContain('var(--k-tint-amber)');
    await screen.findByText('₹9,00,000.00');
  });

  it('has a way back to LIVE', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<SimBanner />);
    fireEvent.click(screen.getByRole('button', { name: 'Back to LIVE' }));
    expect(useViewStore.getState().view).toBe('live');
  });

  it('opens the status card with Details, and closes it again', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<SimBanner />);
    await screen.findByText('₹9,00,000.00');
    expect(screen.queryByTestId('sim-status-card')).toBeNull();
    const toggle = screen.getByRole('button', { name: 'Details' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(screen.getByTestId('sim-status-card')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Reset simulation' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
    expect(screen.queryByTestId('sim-status-card')).toBeNull();
  });

  it('shows the run state next to the title', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<SimBanner />);
    await waitFor(() => expect(screen.getByTestId('sim-run-chip')).toHaveTextContent('Running'));
  });

  it('keeps the banner and the way back up when the virtual account cannot load, saying why', async () => {
    get.mockRejectedValue(apiError(503, { code: 'tenant_unavailable', message: 'x' }));
    renderWithClient(<SimBanner />);
    expect(await screen.findByText(/not available right now/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to LIVE' })).toBeInTheDocument();
    expect(useViewStore.getState().view).toBe('simulation');
  });

  it('returns to LIVE by itself when this login may not open the Simulation view', async () => {
    get.mockRejectedValue(apiError(403, 'Simulation view is available to the owner only'));
    renderWithClient(<SimBanner />);
    await waitFor(() => expect(useViewStore.getState().view).toBe('live'));
  });
});

describe('SimBanner in the live view', () => {
  it('shows nothing at all when no strategy is in Simulation', async () => {
    get.mockResolvedValue(makeStatus({ strategies: strategies({ supertrend: 'AUTO' }) }));
    const { container } = renderWithClient(<SimBanner />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('sim-banner')).toBeNull();
  });

  it('shows a small chip naming the strategy when one is in Simulation, and the chip opens the Simulation view', async () => {
    get.mockResolvedValue(makeStatus({ strategies: strategies({ gamma: 'SIMULATION' }) }));
    renderWithClient(<SimBanner />);
    const chip = await screen.findByRole('button', { name: /Simulation is on for Gamma Unwind/ });
    expect(chip).toHaveTextContent('Gamma Unwind is in Simulation');
    expect(screen.queryByTestId('sim-banner')).toBeNull();
    fireEvent.click(chip);
    expect(useViewStore.getState().view).toBe('simulation');
  });

  it('counts the strategies when several are in Simulation', async () => {
    get.mockResolvedValue(makeStatus({ strategies: strategies({ supertrend: 'SIMULATION', snapback: 'SIMULATION' }) }));
    renderWithClient(<SimBanner />);
    const chip = await screen.findByRole('button', { name: /Supertrend, Snapback/ });
    expect(within(chip).getByText(/2 strategies in Simulation/)).toBeInTheDocument();
  });

  it('shows no chip for a non-owner (status 403)', async () => {
    get.mockRejectedValue(apiError(403, 'Admin access required'));
    const { container } = renderWithClient(<SimBanner />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});

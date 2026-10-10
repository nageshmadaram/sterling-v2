import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';

const get = vi.fn();
const put = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), put: (...a: unknown[]) => put(...a), post: vi.fn() } }));
const notify = vi.fn();
vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: (...a: unknown[]) => notify(...a) }));

import { StrategyModeSelector } from '../StrategyModeSelector';
import { useViewStore } from '../../../store/useViewStore';
import { apiError, makeStatus, renderWithClient, strategies } from './simTestUtils';

const group = (name: string) => screen.getByRole('group', { name });
const modeBtn = (strategy: string, mode: string) => within(group(`${strategy} mode`)).getByRole('button', { name: mode });

/** The groups render at once; the buttons enable when the status has arrived. */
const loaded = () => waitFor(() => expect(modeBtn('Supertrend', 'MANUAL')).toBeEnabled());

beforeEach(() => {
  [get, put, notify].forEach((m) => m.mockReset());
  useViewStore.setState({ view: 'live' });
  get.mockResolvedValue(makeStatus({ strategies: strategies({ supertrend: 'SIMULATION', gamma: 'AUTO' }) }));
});
afterEach(() => { cleanup(); useViewStore.setState({ view: 'live' }); });

describe('StrategyModeSelector in the live view', () => {
  it('shows a 3-way control per strategy and reflects the status modes', async () => {
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    expect(modeBtn('Supertrend', 'SIMULATION')).toHaveAttribute('aria-pressed', 'true');
    expect(modeBtn('Supertrend', 'MANUAL')).toHaveAttribute('aria-pressed', 'false');
    expect(modeBtn('Snapback', 'MANUAL')).toHaveAttribute('aria-pressed', 'true');
    expect(modeBtn('Gamma Unwind', 'AUTO')).toHaveAttribute('aria-pressed', 'true');
    // Plain-language line under each, with the money named.
    expect(within(screen.getByTestId('mode-row-supertrend')).getByText(/virtual money\. No real orders/)).toBeInTheDocument();
    expect(within(screen.getByTestId('mode-row-gamma')).getByText(/REAL money/)).toBeInTheDocument();
  });

  it('disables the controls until the status is known', () => {
    get.mockReturnValue(new Promise(() => {}));
    renderWithClient(<StrategyModeSelector />);
    expect(modeBtn('Supertrend', 'MANUAL')).toBeDisabled();
    expect(modeBtn('Supertrend', 'AUTO')).toBeDisabled();
  });

  it('SIMULATION asks first, says it is virtual money, and sends nothing on Cancel', async () => {
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'SIMULATION'));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/virtual money/i);
    expect(dialog).toHaveTextContent(/No real orders are sent/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it('SIMULATION confirmed PUTs the mode to that strategy\'s route', async () => {
    put.mockResolvedValue({ strategy: 'snapback', mode: 'SIMULATION', strategies: strategies({ snapback: 'SIMULATION' }) });
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'SIMULATION'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Start Simulation' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/sim/strategy/snapback/mode', { mode: 'SIMULATION' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('Snapback is now SIMULATION') }));
  });

  it('AUTO asks first and says in capitals it is REAL money on the Zerodha account', async () => {
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'AUTO'));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/REAL orders on your Zerodha account/);
    expect(dialog).toHaveTextContent(/real money/);
    expect(put).not.toHaveBeenCalled();
    // Focus starts on the safe choice.
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });

  it('AUTO confirmed PUTs AUTO', async () => {
    put.mockResolvedValue({ strategy: 'snapback', mode: 'AUTO', strategies: strategies({ snapback: 'AUTO' }) });
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'AUTO'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /Arm AUTO with real money/ }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/sim/strategy/snapback/mode', { mode: 'AUTO' }));
  });

  it('Escape closes the dialog without sending anything', async () => {
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'AUTO'));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it('MANUAL switches straight off with no dialog', async () => {
    put.mockResolvedValue({ strategy: 'supertrend', mode: 'MANUAL', strategies: strategies() });
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Supertrend', 'MANUAL'));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/sim/strategy/supertrend/mode', { mode: 'MANUAL' }));
  });

  it('does not re-send the mode a strategy is already in', async () => {
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Supertrend', 'SIMULATION'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(put).not.toHaveBeenCalled();
  });

  it('lists the readiness blockers when the server refuses (409)', async () => {
    put.mockRejectedValue(apiError(409, {
      error: 'auto_execute_blocked', code: 'auto_execute_blocked', message: 'blocked',
      reasons: ['Broker session is not logged in', 'Stop-loss protection is off'],
    }));
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'AUTO'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /Arm AUTO/ }));
    const alert = await screen.findByTestId('mode-error-snapback');
    expect(alert).toHaveTextContent('Fix these first');
    expect(within(alert).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Broker session is not logged in', 'Stop-loss protection is off',
    ]);
    expect(screen.queryByRole('dialog')).toBeNull();
    // The strategy still shows its old mode.
    expect(modeBtn('Snapback', 'MANUAL')).toHaveAttribute('aria-pressed', 'true');
  });

  it('says the virtual account is unavailable on a 503', async () => {
    put.mockRejectedValue(apiError(503, { code: 'tenant_unavailable', message: 'x' }));
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'SIMULATION'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Start Simulation' }));
    expect(await screen.findByTestId('mode-error-snapback')).toHaveTextContent(/not available right now/i);
  });

  it('says a partial failure left the strategy OFF on a 500', async () => {
    put.mockRejectedValue(apiError(500, { code: 'mode_change_failed', message: 'x' }));
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    fireEvent.click(modeBtn('Snapback', 'SIMULATION'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Start Simulation' }));
    expect(await screen.findByTestId('mode-error-snapback')).toHaveTextContent(/left OFF/);
  });

  it('warns when both the real and the virtual side are armed', async () => {
    get.mockResolvedValue(makeStatus({ strategies: strategies({ supertrend: 'AUTO' }, ['supertrend']) }));
    renderWithClient(<StrategyModeSelector />);
    await loaded();
    expect(within(screen.getByTestId('mode-row-supertrend')).getByRole('alert')).toHaveTextContent(/Both real and virtual/);
  });

  it('renders nothing for someone who is not the owner', async () => {
    get.mockRejectedValue(apiError(403, 'Admin access required'));
    const { container } = renderWithClient(<StrategyModeSelector />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe('StrategyModeSelector in the Simulation view', () => {
  beforeEach(() => { useViewStore.setState({ view: 'simulation' }); });

  it('is a read-only copy: each mode as a badge, no buttons, and where to change it', async () => {
    renderWithClient(<StrategyModeSelector />);
    await waitFor(() => expect(screen.getByTestId('mode-badge-supertrend')).toHaveTextContent('SIMULATION'));
    expect(screen.getByTestId('mode-badge-snapback')).toHaveTextContent('MANUAL');
    expect(screen.getByTestId('mode-badge-gamma')).toHaveTextContent('AUTO');
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText(/Change a strategy.s mode from the LIVE view/)).toBeInTheDocument();
  });
});

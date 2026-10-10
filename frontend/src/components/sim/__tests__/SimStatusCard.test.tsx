import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';

const put = vi.fn();
const post = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: vi.fn(), put: (...a: unknown[]) => put(...a), post: (...a: unknown[]) => post(...a) } }));
const notify = vi.fn();
vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: (...a: unknown[]) => notify(...a) }));

import { SimStatusCard, parseCapital } from '../SimStatusCard';
import { NOW, apiError, makeStatus, renderWithClient } from './simTestUtils';

const fresh = (over = {}) => makeStatus({
  runtime: { running: true, last_pass_ms: NOW - 2_000, passes: 5, last_error: '', session_open: true }, ...over,
});

beforeEach(() => {
  [put, post, notify].forEach((m) => m.mockReset());
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('SimStatusCard numbers', () => {
  it('shows capital, cash, equity, today\'s P&L, open positions and the last price check, each explained', () => {
    renderWithClient(<SimStatusCard status={fresh()} />);
    const card = screen.getByTestId('sim-status-card');
    expect(within(card).getByText('₹10,00,000.00')).toBeInTheDocument();         // capital
    expect(within(card).getByText('₹9,00,000.00')).toBeInTheDocument();          // cash
    expect(within(card).getByText('₹10,02,500.00')).toBeInTheDocument();         // equity
    expect(within(card).getByText('+₹2,500.00')).toBeInTheDocument();            // today's P&L, signed
    expect(within(card).getByText('2')).toBeInTheDocument();                     // open positions
    expect(within(card).getByText('10:29:58 IST')).toBeInTheDocument();          // IST, not the browser's zone
    // One plain line under each label.
    expect(within(card).getByText('Virtual money not tied up in open trades.')).toBeInTheDocument();
    expect(within(card).getByText('Cash plus what the open trades are worth right now.')).toBeInTheDocument();
    expect(within(card).getByText('When the simulation last looked at market prices.')).toBeInTheDocument();
  });

  it('colours a loss with the loss token and prints it signed', () => {
    renderWithClient(<SimStatusCard status={fresh({ today_pnl: -1234.5 })} />);
    const value = screen.getByText('-₹1,234.50');
    expect(value).toHaveStyle({ color: 'var(--k-red)' });
  });

  it('prints a dash, not 0, for a last price check that never happened', () => {
    renderWithClient(<SimStatusCard status={fresh({ runtime: { running: true, last_pass_ms: 0, session_open: true } })} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});

describe('SimStatusCard running indicator', () => {
  const chip = () => screen.getByTestId('sim-run-chip');

  it('says Running when prices were checked a moment ago', () => {
    renderWithClient(<SimStatusCard status={fresh()} />);
    expect(chip()).toHaveTextContent('Running');
    expect(chip()).toHaveAttribute('data-state', 'running');
  });

  it('says Stalled, and for how long, when the last price check is old', () => {
    renderWithClient(<SimStatusCard status={fresh({ runtime: { running: true, last_pass_ms: NOW - 90_000, session_open: true } })} />);
    expect(chip()).toHaveTextContent('Stalled');
    expect(screen.getByText(/No price check for 90 seconds/)).toBeInTheDocument();
  });

  it('says Stopped when the engine is not running', () => {
    renderWithClient(<SimStatusCard status={fresh({ runtime: { running: false, last_pass_ms: 0, session_open: true } })} />);
    expect(chip()).toHaveTextContent('Stopped');
  });

  it('says Market closed out of session instead of calling an idle loop stalled', () => {
    renderWithClient(<SimStatusCard status={fresh({ runtime: { running: true, last_pass_ms: NOW - 3_600_000, session_open: false } })} />);
    expect(chip()).toHaveTextContent('Market closed');
  });

  it('says Paused with the reason when there are no live prices', () => {
    renderWithClient(<SimStatusCard status={fresh({ price_feed: { available: false, detail: 'Broker session is not logged in: simulation is paused.' } })} />);
    expect(chip()).toHaveTextContent('Paused');
    expect(screen.getByText(/Broker session is not logged in/)).toBeInTheDocument();
  });
});

describe('Set virtual capital', () => {
  const open = () => {
    renderWithClient(<SimStatusCard status={fresh()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Set virtual capital' }));
    return screen.getByLabelText('Virtual capital (₹)') as HTMLInputElement;
  };

  it('starts from the current capital', () => {
    expect(open().value).toBe('1000000');
  });

  it.each(['', 'abc', '0', '-5', '12.345'])('refuses %j without calling the server', (text) => {
    const input = open();
    fireEvent.change(input, { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/Enter an amount in rupees/);
    expect(put).not.toHaveBeenCalled();
  });

  it('refuses more than the server allows', () => {
    const input = open();
    fireEvent.change(input, { target: { value: '99999999999' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/The most allowed/);
    expect(put).not.toHaveBeenCalled();
  });

  it('PUTs the amount, understanding Indian digit grouping', async () => {
    put.mockResolvedValue(makeStatus({ capital: 500_000 }));
    const input = open();
    fireEvent.change(input, { target: { value: '5,00,000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(put).toHaveBeenCalledWith('/api/v1/sim/capital', { capital: 500_000 }));
    await waitFor(() => expect(screen.queryByLabelText('Virtual capital (₹)')).toBeNull());
  });

  it('shows the server\'s refusal in plain words and stays open', async () => {
    put.mockRejectedValue(apiError(503, { code: 'tenant_unavailable', message: 'x' }));
    const input = open();
    fireEvent.change(input, { target: { value: '500000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not available right now/i);
    expect(screen.getByLabelText('Virtual capital (₹)')).toBeInTheDocument();
  });

  it('parseCapital accepts rupee signs and commas only', () => {
    expect(parseCapital('₹ 10,00,000')).toBe(1_000_000);
    expect(parseCapital('2500.50')).toBe(2500.5);
    expect(parseCapital('1e6')).toBeNull();
    expect(parseCapital('')).toBeNull();
  });
});

describe('Reset simulation', () => {
  const openDialog = () => {
    renderWithClient(<SimStatusCard status={fresh()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reset simulation' }));
    return screen.getByRole('dialog');
  };

  it('needs the word RESET typed before the button works, and says what is wiped', () => {
    const dialog = openDialog();
    expect(dialog).toHaveTextContent(/virtual/);
    expect(dialog).toHaveTextContent(/real Zerodha account is not touched/);
    const confirm = within(dialog).getByRole('button', { name: 'Reset simulation' });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Type RESET to confirm'), { target: { value: 'rese' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Type RESET to confirm'), { target: { value: 'RESET' } });
    expect(confirm).toBeEnabled();
    expect(post).not.toHaveBeenCalled();
  });

  it('puts focus in the field to type, so the keyboard path is short', () => {
    const dialog = openDialog();
    expect(within(dialog).getByLabelText('Type RESET to confirm')).toHaveFocus();
  });

  it('POSTs {confirm: true} once confirmed and reports success', async () => {
    post.mockResolvedValue({ ok: true, failed: {}, status: makeStatus({ open_positions: 0 }) });
    const dialog = openDialog();
    fireEvent.change(within(dialog).getByLabelText('Type RESET to confirm'), { target: { value: 'RESET' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset simulation' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/sim/reset', { confirm: true }));
    expect(await screen.findByText(/The simulation was reset/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('names the parts that could not be cleared', async () => {
    post.mockResolvedValue({ ok: false, failed: { journal: 'locked' }, status: makeStatus() });
    const dialog = openDialog();
    fireEvent.change(within(dialog).getByLabelText('Type RESET to confirm'), { target: { value: 'RESET' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset simulation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be cleared: journal/);
  });

  it('Cancel closes without a request', () => {
    const dialog = openDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it('keeps Tab inside the dialog', () => {
    const dialog = openDialog();
    const confirm = within(dialog).getByRole('button', { name: 'Reset simulation' });
    fireEvent.change(within(dialog).getByLabelText('Type RESET to confirm'), { target: { value: 'RESET' } });
    confirm.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(within(dialog).getByLabelText('Type RESET to confirm')).toHaveFocus();
  });
});

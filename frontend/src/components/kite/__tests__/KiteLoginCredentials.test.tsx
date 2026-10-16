import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { KiteLoginCredentials } from '../KiteLoginCredentials';
const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../utils/api', () => ({ api }));
beforeEach(() => { vi.clearAllMocks(); api.get.mockResolvedValue({ username: 'AA001', password_saved: true, automatic_login_available: true }); });
afterEach(cleanup);
function show() { render(<QueryClientProvider client={new QueryClient()}><KiteLoginCredentials accountId="second" /></QueryClientProvider>); }
it('shows a masked summary and edits the chosen broker with matching passwords', async () => {
  api.put.mockResolvedValue({ username: 'AA001', password_saved: true, automatic_login_available: true });
  show();
  await screen.findByText('AA001');
  expect(screen.getByText('Password saved')).toBeInTheDocument();
  expect(screen.queryByLabelText('New Kite password')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit login' }));
  expect(screen.getByLabelText('Kite user ID')).toHaveValue('AA001');
  expect(screen.getByLabelText('New Kite password')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('New Kite password'), { target: { value: 'replacement' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Kite login' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Passwords do not match');
  expect(api.put).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Repeat new Kite password'), { target: { value: 'replacement' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Kite login' }));
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/v1/kite/accounts/second/login-credentials', { username: 'AA001', password: 'replacement' }));
  await waitFor(() => expect(screen.queryByLabelText('New Kite password')).toBeNull());
  expect(screen.getByRole('status')).toHaveTextContent('Login details saved.');
});
it('discards unsaved values and password visibility on cancel', async () => {
  show(); await screen.findByText('AA001');
  fireEvent.click(screen.getByRole('button', { name: 'Edit login' }));
  fireEvent.change(screen.getByLabelText('New Kite password'), { target: { value: 'discard-me' } });
  fireEvent.click(screen.getByRole('button', { name: 'Show new Kite password' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'Edit login' }));
  expect(screen.getByLabelText('New Kite password')).toHaveValue('');
  expect(screen.getByLabelText('New Kite password')).toHaveAttribute('type', 'password');
  expect(api.put).not.toHaveBeenCalled();
});

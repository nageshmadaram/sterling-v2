import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { KiteAssistedLogin } from '../KiteAssistedLogin';
const state = vi.hoisted(() => ({ accounts: [{ id: 'one', label: 'Main', kite_user_id: 'AA001' }], api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('../../../utils/api', () => ({ api: state.api }));
vi.mock('../../../hooks/useKite', () => ({ useKiteAccounts: () => ({ data: { accounts: state.accounts } }) }));
beforeEach(() => { vi.clearAllMocks(); state.accounts = [{ id: 'one', label: 'Main', kite_user_id: 'AA001' }]; });
afterEach(cleanup);
function show() { render(<QueryClientProvider client={new QueryClient()}><KiteAssistedLogin /></QueryClientProvider>); }
it('asks for account selection even with one account and submits only its ID', async () => {
  state.api.post.mockResolvedValue({ id: 'attempt', account_id: 'one', username: 'AA001', phase: 'awaiting_totp', message: 'Enter TOTP' });
  show();
  expect(screen.getByRole('button', { name: 'Login with saved credentials' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  await screen.findByLabelText('Kite TOTP');
  expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login', { account_id: 'one' });
  fireEvent.change(screen.getByLabelText('Kite TOTP'), { target: { value: '123456' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verify TOTP' }));
  await waitFor(() => expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login/attempt/totp', { code: '123456' }));
  expect(screen.getByLabelText('Kite TOTP')).toHaveValue('');
});
it('uses the selected second broker when multiple accounts are saved', async () => {
  state.accounts.push({ id: 'two', label: 'Second', kite_user_id: 'BB002' });
  state.api.post.mockResolvedValue({ id: 'attempt', phase: 'failed', message: 'Test stop' });
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'two' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  await waitFor(() => expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login', { account_id: 'two' }));
});
it('shows a masked live broker frame and removes it when login is cancelled', async () => {
  const attempt = { id: 'preview-attempt', account_id: 'one', username: 'AA001', phase: 'awaiting_totp', message: 'Enter TOTP' };
  state.api.post.mockResolvedValue(attempt);
  state.api.get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/preview')
    ? { image: 'data:image/png;base64,AAAA', captured_at_ms: 1000 }
    : attempt));
  state.api.delete.mockResolvedValue(undefined);
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  await screen.findByRole('button', { name: 'Show live browser view' });
  expect(screen.queryByRole('img')).toBeNull();
  expect(state.api.get).not.toHaveBeenCalledWith(expect.stringContaining('/preview'));
  fireEvent.click(screen.getByRole('button', { name: 'Show live browser view' }));
  const image = await screen.findByRole('img', { name: 'Live Kite login page with credential fields hidden' });
  expect(image).toHaveAttribute('src', 'data:image/png;base64,AAAA');
  expect(screen.getByText('Waiting for your TOTP…').closest('li')).toHaveAttribute('aria-current', 'step');
  expect(screen.getByText(/Password and OTP fields hidden/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Hide live browser view' }));
  await waitFor(() => expect(screen.queryByRole('img')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Cancel broker login' }));
  await waitFor(() => expect(screen.queryByRole('img')).toBeNull());
});
it('tests saved credentials without asking for TOTP or claiming broker connection', async () => {
  state.api.post.mockResolvedValue({ id: 'test', account_id: 'one', username: 'AA001', phase: 'tested', credential_status: 'accepted', test_only: true, message: 'Kite accepted the saved user ID and password. TOTP is still required. No broker session was created.' });
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Test saved credentials' }));
  await waitFor(() => expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login', { account_id: 'one', test_only: true }));
  expect(await screen.findByRole('status')).toHaveTextContent('Kite accepted the saved user ID and password');
  expect(screen.queryByLabelText('Kite TOTP')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('No broker session was created');
});
it('labels the current Kite Mobile App Code challenge instead of asking for the wrong authenticator', async () => {
  state.api.post.mockResolvedValue({ id: 'mobile-code', account_id: 'one', username: 'AA001', phase: 'awaiting_totp', credential_status: 'accepted', verification_method: 'Kite Mobile App Code', message: 'Enter the Mobile App Code shown in your Kite mobile app.' });
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  expect(await screen.findByLabelText('Kite Mobile App Code')).toBeInTheDocument();
  expect(screen.queryByLabelText('Kite TOTP')).toBeNull();
  expect(screen.getByRole('button', { name: 'Verify App Code' })).toBeDisabled();
  expect(screen.getByText('Waiting for your Mobile App Code…').closest('li')).toHaveAttribute('aria-current', 'step');
  expect(screen.queryByText('Waiting for your TOTP…')).toBeNull();
});

it('auto-submits six digits exactly once and waits through an in-flight submission', async () => {
  const attempt = { id: 'auto', account_id: 'one', username: 'AA001', phase: 'awaiting_totp', message: 'Enter code' };
  state.api.post.mockResolvedValueOnce(attempt).mockImplementationOnce(() => new Promise(() => {}));
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  const input = await screen.findByLabelText('Kite TOTP');
  fireEvent.change(input, { target: { value: '12345' } });
  expect(state.api.post).toHaveBeenCalledTimes(1);
  fireEvent.change(input, { target: { value: '123456' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Enter Kite verification code' }));
  expect(state.api.post).toHaveBeenCalledTimes(2);
  expect(state.api.post).toHaveBeenLastCalledWith('/api/v1/kite/assisted-login/auto/totp', { code: '123456' });
  expect(input).toBeDisabled();
  expect(input).toHaveValue('');
});
it('requests fresh preview frames while expanded and stops on collapse', async () => {
  const attempt = { id: 'frames', account_id: 'one', username: 'AA001', phase: 'awaiting_totp', message: 'Enter code' };
  let frames = 0;
  state.api.post.mockResolvedValue(attempt);
  state.api.get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/preview')
    ? { image: 'data:image/png;base64,' + (++frames === 1 ? 'AAAA' : 'BBBB'), captured_at_ms: Date.now() } : attempt));
  show();
  fireEvent.change(screen.getByLabelText('Broker account'), { target: { value: 'one' } });
  fireEvent.click(screen.getByRole('button', { name: 'Login with saved credentials' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Show live browser view' }));
  await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', 'data:image/png;base64,BBBB'), { timeout: 2500 });
  fireEvent.click(screen.getByRole('button', { name: 'Hide live browser view' }));
  expect(screen.queryByRole('img')).toBeNull();
});
it('starts a Telegram-requested owned account once after account data is available', async () => {
  state.api.post.mockResolvedValue({ id: 'from-link', account_id: 'one', phase: 'failed', message: 'Stopped' });
  render(<QueryClientProvider client={new QueryClient()}><KiteAssistedLogin accountId="one" autoStart /></QueryClientProvider>);
  await waitFor(() => expect(state.api.post).toHaveBeenCalledTimes(1));
  expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login', { account_id: 'one' });
});
it('never auto-starts an account absent from the signed-in profile', () => {
  render(<QueryClientProvider client={new QueryClient()}><KiteAssistedLogin accountId="foreign-account" autoStart /></QueryClientProvider>);
  expect(state.api.post).not.toHaveBeenCalled();
});

it('Telegram starts saved-credential login after the user chooses their account', async () => {
  state.api.post.mockResolvedValue({id:'chosen',phase:'failed',message:'Stopped'});
  render(<QueryClientProvider client={new QueryClient()}><KiteAssistedLogin autoStart /></QueryClientProvider>);
  expect(state.api.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Broker account'), {target:{value:'one'}});
  await waitFor(()=>expect(state.api.post).toHaveBeenCalledWith('/api/v1/kite/assisted-login',{account_id:'one'}));
  expect(state.api.post).toHaveBeenCalledTimes(1);
});

it('refreshes broker status after a saved session without falsely declaring connection', async () => {
  const started = {
    id: 'saved-attempt', account_id: 'one', username: 'AA001',
    phase: 'awaiting_totp', message: 'Enter code',
  };
  const finished = {
    ...started, phase: 'done',
    message: 'Kite login successful for AA001. Broker session saved.',
  };
  state.api.post.mockResolvedValue(started);
  state.api.get.mockResolvedValue(finished);
  const saved = vi.fn();
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}>
    <KiteAssistedLogin onSaved={saved} />
  </QueryClientProvider>);
  fireEvent.change(screen.getByLabelText('Broker account'), {target: {value: 'one'}});
  fireEvent.click(screen.getByRole('button', {name: 'Login with saved credentials'}));
  await waitFor(() => expect(saved).toHaveBeenCalledWith('one'), {timeout: 3500});
  expect(saved).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Broker session saved/)).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Login with saved credentials'})).toBeNull();
});

it('refreshes status on an immediate done response from the six-digit verification request', async () => {
  const waiting = {id: 'direct-done', account_id: 'one', username: 'AA001',
    phase: 'awaiting_totp', message: 'Enter app code'};
  const saved = vi.fn();
  state.api.post.mockResolvedValueOnce(waiting).mockResolvedValueOnce({
    ...waiting, phase: 'done', message: 'Kite login successful. Broker session saved.',
  });
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(<QueryClientProvider client={client}>
    <KiteAssistedLogin onSaved={saved} />
  </QueryClientProvider>);
  fireEvent.change(screen.getByLabelText('Broker account'), {target:{value:'one'}});
  fireEvent.click(screen.getByRole('button', {name:'Login with saved credentials'}));
  const input = await screen.findByLabelText('Kite TOTP');
  fireEvent.change(input, {target:{value:'123456'}});
  await waitFor(() => expect(saved).toHaveBeenCalledExactlyOnceWith('one'));
  expect(invalidate).toHaveBeenCalledWith({queryKey:['kite-status']});
  expect(invalidate).toHaveBeenCalledWith({queryKey:['kite-accounts']});
  expect(screen.getByText(/Broker session saved/)).toBeInTheDocument();
});

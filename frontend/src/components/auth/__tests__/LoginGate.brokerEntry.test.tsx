import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { loginSterling } from '../../../utils/auth';
import { LoginGate } from '../LoginGate';
const get = vi.hoisted(() => vi.fn());
vi.mock('../../../utils/api', () => ({api:{get}}));
vi.mock('../../../utils/auth', () => ({clearAuthTokens:vi.fn(),loginSterling:vi.fn(),rememberSterlingSession:vi.fn()}));
vi.mock('../BrokerLoginEntry', () => ({BrokerLoginEntry:()=> <div>Authenticated broker entry</div>}));
afterEach(() => {cleanup();vi.resetAllMocks();window.history.replaceState({}, '', '/');});
it('does not mount broker entry until Sterling verifies its session', async () => {
  let verify!: (value:unknown)=>void;
  get.mockReturnValue(new Promise(resolve=>{verify=resolve;}));
  render(<QueryClientProvider client={new QueryClient()}><LoginGate><div>App</div></LoginGate></QueryClientProvider>);
  expect(screen.queryByText('Authenticated broker entry')).toBeNull();
  verify({user_id:'owner'});
  await screen.findByText('Authenticated broker entry');
});
it('keeps broker entry unmounted when Sterling sign-in is required', async () => {
  get.mockRejectedValue(Object.assign(new Error('Sign in'),{status:401}));
  render(<QueryClientProvider client={new QueryClient()}><LoginGate><div>App</div></LoginGate></QueryClientProvider>);
  await waitFor(()=>expect(screen.getByRole('form',{name:'Sterling login'})).toBeInTheDocument());
  expect(screen.queryByText('Authenticated broker entry')).toBeNull();
});

it('Telegram sign-in explicitly asks for username and password even when a session could be restored', async () => {
  window.history.replaceState({}, '', '/?sterling_login=1&broker_login=1');
  get.mockResolvedValue({user_id:'owner'});
  render(<QueryClientProvider client={new QueryClient()}><LoginGate><div>App</div></LoginGate></QueryClientProvider>);
  expect(screen.getByRole('form',{name:'Sterling login'})).toBeInTheDocument();
  expect(get).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Username'),{target:{value:'synthetic-owner'}});
  fireEvent.change(screen.getByLabelText('Password'),{target:{value:'synthetic-password'}});
  fireEvent.click(screen.getByRole('button',{name:'Sign in'}));
  await screen.findByText('Authenticated broker entry');
  expect(loginSterling).toHaveBeenCalledWith('synthetic-owner','synthetic-password');
  expect(window.location.search).toBe('?broker_login=1');
});
it('does not open the broker picker when explicit Sterling sign-in fails', async () => {
  window.history.replaceState({}, '', '/?sterling_login=1&broker_login=1');
  vi.mocked(loginSterling).mockRejectedValue(new Error('Invalid username or password'));
  render(<QueryClientProvider client={new QueryClient()}><LoginGate><div>App</div></LoginGate></QueryClientProvider>);
  fireEvent.change(screen.getByLabelText('Username'),{target:{value:'synthetic-owner'}});
  fireEvent.change(screen.getByLabelText('Password'),{target:{value:'incorrect-password'}});
  fireEvent.click(screen.getByRole('button',{name:'Sign in'}));
  await screen.findByRole('alert');
  expect(screen.queryByText('Authenticated broker entry')).toBeNull();
  expect(get).not.toHaveBeenCalled();
});

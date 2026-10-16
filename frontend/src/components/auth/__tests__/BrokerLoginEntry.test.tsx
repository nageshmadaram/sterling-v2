import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrokerLoginEntry } from '../BrokerLoginEntry';
import { useKiteStatusCardStore } from '../../../store/useKiteStatusCardStore';
const get = vi.hoisted(() => vi.fn());
vi.mock('../../../utils/api', () => ({ api: { get } }));
beforeEach(() => {
  get.mockReset();
  useKiteStatusCardStore.setState({ isOpen: false, loginAccountId: null, loginRequested: false });
  window.history.replaceState({}, '', '/?broker_login=1&broker_account=linked&broker_profile=owner');
});
afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); });
it('opens the owned assisted login after Sterling identity is confirmed', async () => {
  get.mockResolvedValueOnce({user_id:'owner'}).mockResolvedValueOnce({accounts:[{id:'linked'}]});
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  await waitFor(() => expect(useKiteStatusCardStore.getState().loginAccountId).toBe('linked'));
  expect(useKiteStatusCardStore.getState().isOpen).toBe(true);
  expect(window.location.search).toBe('');
});
it('rejects a link opened by another Sterling profile', async () => {
  get.mockResolvedValue({user_id:'someone-else'});
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Sign in with the Sterling profile');
  expect(get).toHaveBeenCalledTimes(1);
  expect(useKiteStatusCardStore.getState().loginAccountId).toBeNull();
});
it('rejects accounts not linked to the signed-in profile', async () => {
  get.mockResolvedValueOnce({user_id:'owner'}).mockResolvedValueOnce({accounts:[{id:'other-account'}]});
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('not linked to your Sterling profile');
  expect(useKiteStatusCardStore.getState().loginAccountId).toBeNull();
});
it('does nothing until a user opens a broker login link', async () => {
  window.history.replaceState({}, '', '/');
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  expect(get).not.toHaveBeenCalled();
});

it('a profile-neutral Telegram link opens the signed-in profile account picker', async () => {
  window.history.replaceState({}, '', '/?broker_login=1');
  get.mockResolvedValueOnce({user_id:'a-different-sterling-user'}).mockResolvedValueOnce({accounts:[{id:'their-broker'}]});
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  await waitFor(()=>expect(useKiteStatusCardStore.getState().loginRequested).toBe(true));
  expect(useKiteStatusCardStore.getState().loginAccountId).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});
it('does not open broker login without a verified Sterling identity', async () => {
  window.history.replaceState({}, '', '/?broker_login=1');
  get.mockResolvedValue({});
  render(<QueryClientProvider client={new QueryClient()}><BrokerLoginEntry /></QueryClientProvider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Sign in to Sterling before');
  expect(useKiteStatusCardStore.getState().loginRequested).toBe(false);
  expect(get).toHaveBeenCalledTimes(1);
});

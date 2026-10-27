import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Administration from '../../../pages/Administration';
const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('../../../utils/api', () => ({ api }));
// An inherited simulation preference must not block identity administration.
vi.mock('../../../store/useViewStore', () => ({ useIsSimulation: () => true }));
const sections = { brokers: { visible: false, grants: [] }, telegram: { visible: false, grants: [] }, integrations: { visible: false, grants: [] } };
const capabilities = { user_id: 'default', revision: 0, administrator: true, active: true, sections };
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockImplementation(async (url: string) => {
    if (url === '/api/v1/admin/access-requests') return {requests: []};
    if (url === '/api/v1/settings/capabilities') return capabilities;
    if (url === '/api/v1/auth/users') return { current_user_id: 'default', users: [
      { id: 'default', username: 'Primary', primary: true, role: 'admin', active: true, password_set: true },
      { id: 'member', username: 'Member', role: 'user', active: true, password_set: true },
    ] };
    if (url.endsWith('/features')) return { user_id: 'member', revision: 0, features: { kite: false, tv: false, radio: false } };
    if (url.endsWith('/configuration-access')) return { ...capabilities, user_id: 'member', administrator: false };
    throw new Error(`Unexpected API ${url}`);
  });
});
afterEach(cleanup);
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Administration /></QueryClientProvider>); }
it('opens user administration without Kite, even with a remembered simulation preference', async () => {
  show();
  await screen.findByRole('button', { name: 'Manage Primary' });
  expect(screen.getByRole('button', { name: /Add user/ })).toBeInTheDocument();
  expect(screen.getAllByText('Active').length).toBeGreaterThan(0);
  expect(api.get.mock.calls.every(([url]) => !url.includes('/kite'))).toBe(true);
});
it('saves explicit grants with the loaded revision and keeps default configuration hidden', async () => {
  show();
  await screen.findByRole('button', { name: 'Manage Primary' });
  fireEvent.focus(screen.getByRole('combobox', { name: 'Manage user access' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Manage user access' }), { target: { value: 'Mem' } });
  fireEvent.click(screen.getByRole('option', { name: /Member/ }));
  await screen.findByRole('button', { name: 'Save features' });
  expect(screen.getAllByText('Hidden; configuration actions are disabled.')).toHaveLength(3);
  fireEvent.click(screen.getByLabelText('Space (TV)', { exact: true }));
  api.put.mockResolvedValue({ user_id: 'member', revision: 1, features: { kite: false, tv: true, radio: false } });
  fireEvent.click(screen.getByRole('button', { name: 'Save features' }));
  await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/v1/auth/users/member/features', {
    features: { kite: false, tv: true, radio: false }, expected_revision: 0,
  }));
  expect(api.get.mock.calls.every(([url]) => !url.includes('/kite'))).toBe(true);
});
it('denies ordinary users without requesting the user directory', async () => {
  api.get.mockResolvedValue({ ...capabilities, administrator: false });
  show();
  await screen.findByRole('alert');
  expect(api.get).not.toHaveBeenCalledWith('/api/v1/auth/users');
});

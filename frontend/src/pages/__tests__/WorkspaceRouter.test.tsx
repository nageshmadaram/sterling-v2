import { cleanup, fireEvent, render, screen, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setDraftDirty, resetDraftGuard } from '../../components/kite/config/unsavedDraftGuard';
import { WorkspaceRouter, type SessionPolicy } from '../WorkspaceRouter';
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../utils/api', () => ({ api }));
vi.mock('../KiteRuntime', () => ({ default: () => <div>Kite runtime mounted</div> }));
vi.mock('../MediaWorkspace', () => ({ default: ({ feature }: { feature: string }) => <div>{feature} runtime mounted</div> }));
vi.mock('../Administration', () => ({ default: () => <div>Central administration mounted</div> }));
vi.mock('../../components/auth/PreferenceGate', () => ({ PreferenceGate: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../components/kite/ThemeToggle', () => ({ ThemeToggle: () => <button>Theme</button> }));
vi.mock('../../components/kite/ProfilePhotoCropper', () => ({ ProfilePhotoCropper: () => <div>Selected photo crop preserved</div> }));
const base: SessionPolicy = { user_id: 'member', username: 'Member', role: 'user', administrator: false, policy_revision: 0, features: { kite: false, tv: false, radio: false } };
beforeEach(() => {
  vi.resetAllMocks(); window.history.replaceState({}, '', '/'); vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  api.post.mockResolvedValue({ status: 'pending' });
});
afterEach(() => { cleanup(); resetDraftGuard(); vi.restoreAllMocks(); });
function show(policy: SessionPolicy) {
  api.get.mockResolvedValue(policy);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><WorkspaceRouter /></QueryClientProvider>); return client;
}
for (let mask = 0; mask < 8; mask++) it(`offers granted workspaces and access requests for combination ${mask}`, async () => {
  const policy = { ...base, features: { kite: !!(mask & 1), tv: !!(mask & 2), radio: !!(mask & 4) } }; show(policy);
  await screen.findByRole('heading', { name: 'Choose your workspace' });
  for (const [feature, name] of [['kite', 'Kite'], ['tv', 'Space'], ['radio', 'Echo']] as const) {
    expect(screen.getByRole('button', { name: `${policy.features[feature] ? 'Open' : 'Request access to'} ${name}` })).toBeInTheDocument();
  }
  expect(screen.queryByText(/runtime mounted/)).toBeNull();
});
it('revokes a mounted feature without sending a request until explicitly clicked', async () => {
  const policy = { ...base, features: { kite: false, tv: true, radio: false } }; const client = show(policy);
  fireEvent.click(await screen.findByRole('button', { name: 'Open Space' })); await screen.findByText('tv runtime mounted');
  act(() => client.setQueryData(['sterling-session-policy'], { ...policy, features: base.features }));
  await screen.findByRole('heading', { name: 'Space is not enabled for your account' });
  expect(screen.queryByText('tv runtime mounted')).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '← Back to workspaces' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Request access to Space' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
  expect(api.post).toHaveBeenCalledWith('/api/v1/settings/access-requests', { feature: 'tv' });
});

it('shows a back button above the access screen and never requests when navigating back', async () => {
  show({ ...base });
  fireEvent.click(await screen.findByRole('button', { name: 'Request access to Kite' }));
  expect(await screen.findByRole('heading', { name: 'Kite is not enabled for your account' })).toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '← Back to workspaces' }));
  expect(await screen.findByRole('heading', { name: 'Choose your workspace' })).toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
});

it('does not expose hidden Kite in the chooser, including direct URL requests', async () => {
  const visibility = {kite:false,radio:true,tv:true};
  const policy = {...base,features:{kite:false,radio:true,tv:true},visibility};
  show(policy);
  await screen.findByRole('heading',{name:'Choose your workspace'});
  expect(screen.queryByRole('button',{name:/Kite/})).toBeNull();
  expect(screen.getByRole('button',{name:'Open Echo'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Open Space'})).toBeInTheDocument();
  act(()=>window.dispatchEvent(new CustomEvent('sterling-navigate',{detail:'kite'})));
  await screen.findByRole('heading',{name:'Workspace unavailable'});
  expect(screen.queryByRole('button',{name:/Request access to Kite/})).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

it('opens admin management directly, with no empty-feature message', async () => {
  show({ ...base, role: 'admin', administrator: true }); await screen.findByText('Central administration mounted');
  expect(screen.queryByText('No features enabled')).toBeNull(); expect(screen.queryByText('Kite runtime mounted')).toBeNull();
});
it('uses an overlay to switch and removes the duplicate header in Kite', async () => {
  show({ ...base, features: { kite: true, tv: true, radio: true } });
  fireEvent.click(await screen.findByRole('button', { name: 'Open Kite' })); await screen.findByText('Kite runtime mounted');
  expect(document.querySelector('.workspace-header')).toBeNull();
  act(() => window.dispatchEvent(new Event('sterling-select-workspace')));
  expect(screen.getByRole('dialog', { name: 'Choose your workspace' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open Echo' })); await screen.findByText('radio runtime mounted');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.queryByRole('navigation', { name: 'Workspace navigation' })).toBeNull();
});
it('preserves a settings draft when selecting another UI is cancelled', async () => {
  show({ ...base, features: { kite: true, tv: true, radio: true } });
  fireEvent.click(await screen.findByRole('button', { name: 'Open Kite' })); await screen.findByText('Kite runtime mounted');
  setDraftDirty('workspace-test', true); vi.spyOn(window, 'confirm').mockReturnValue(false);
  act(() => window.dispatchEvent(new Event('sterling-select-workspace')));
  fireEvent.click(screen.getByRole('button', { name: 'Open Echo' }));
  expect(screen.getByText('Kite runtime mounted')).toBeInTheDocument(); expect(window.location.pathname).toBe('/kite');
});

it('preserves a selected photo during a failed policy poll while closing the protected workspace', async () => {
  const policy = { ...base, features: { kite: true, tv: true, radio: true } };
  const client = show(policy);
  fireEvent.click(await screen.findByRole('button', { name: 'Open Kite' }));
  await screen.findByText('Kite runtime mounted');
  act(() => window.dispatchEvent(new Event('sterling-open-profile')));
  fireEvent.change(await screen.findByLabelText('Choose profile picture'), {
    target: { files: [new File(['synthetic photo'], 'portrait.png', { type: 'image/png' })] },
  });
  expect(await screen.findByRole('dialog', { name: 'Crop profile picture' })).toBeInTheDocument();
  api.get.mockRejectedValue(new Error('Connection interrupted'));
  await act(async () => { await client.refetchQueries({ queryKey: ['sterling-session-policy'] }); });
  await waitFor(() => expect(screen.queryByText('Kite runtime mounted')).toBeNull());
  expect(screen.getByText('Access could not be verified')).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'Crop profile picture' })).toHaveTextContent('Selected photo crop preserved');
  api.get.mockResolvedValue(policy);
  await act(async () => { await client.refetchQueries({ queryKey: ['sterling-session-policy'] }); });
  expect(await screen.findByText('Kite runtime mounted')).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'Crop profile picture' })).toBeInTheDocument();
});

it('cycles Echo and Space only through permitted destinations, and restores a minimized header', async () => {
  show({ ...base, features: { kite: false, tv: true, radio: true } });
  fireEvent.click(await screen.findByRole('button',{name:'Open Echo'}));
  await screen.findByText('radio runtime mounted');
  fireEvent.click(screen.getByRole('button',{name:'Switch workspace from Echo'}));
  await screen.findByText('tv runtime mounted');
  expect(window.location.pathname).toBe('/tv');
  fireEvent.click(screen.getByRole('button',{name:'Minimize media header'}));
  expect(document.querySelector('.workspace-shell')).toHaveClass('media-header-minimized');
  fireEvent.click(screen.getByRole('button',{name:'Show media header'}));
  expect(document.querySelector('.workspace-shell')).not.toHaveClass('media-header-minimized');
  fireEvent.click(screen.getByRole('button',{name:'Switch workspace from Space'}));
  await screen.findByText('radio runtime mounted');
  expect(window.location.pathname).toBe('/radio');
});

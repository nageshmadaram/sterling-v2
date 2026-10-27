import { cleanup, fireEvent, render, screen, act } from '@testing-library/react';
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
const base: SessionPolicy = { user_id: 'member', username: 'Member', role: 'user', administrator: false, policy_revision: 0, features: { kite: false, tv: false, radio: false } };
beforeEach(() => { vi.resetAllMocks(); window.history.replaceState({}, '', '/'); vi.spyOn(window, 'scrollTo').mockImplementation(() => {}); });
afterEach(() => { cleanup(); resetDraftGuard(); vi.restoreAllMocks(); });
function show(policy: SessionPolicy) {
  api.get.mockResolvedValue(policy);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><WorkspaceRouter /></QueryClientProvider>);
  return client;
}
for (let mask = 0; mask < 8; mask++) {
  it(`shows the chooser without mounting a feature for grant combination ${mask}`, async () => {
    const policy = { ...base, features: { kite: !!(mask & 1), tv: !!(mask & 2), radio: !!(mask & 4) } };
    show(policy);
    await screen.findByText('Welcome, Member.');
    for (const [feature, name] of [['kite', 'Kite'], ['tv', 'Space'], ['radio', 'Echo']] as const)
      expect(screen.queryByRole('button', { name: new RegExp(`Open ${name}`) }) !== null).toBe(policy.features[feature]);
    expect(screen.queryByText(/runtime mounted/)).toBeNull();
    if (!mask) expect(screen.getByText('No features enabled')).toBeInTheDocument();
  });
}
it('lets a TV-only user open TV and unmounts it on revocation', async () => {
  const policy = { ...base, features: { kite: false, tv: true, radio: false } };
  const client = show(policy);
  fireEvent.click(await screen.findByRole('button', { name: /Open Space/ }));
  await screen.findByText('tv runtime mounted');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
  act(() => client.setQueryData(['sterling-session-policy'], { ...policy, features: base.features }));
  await screen.findByText('Access unavailable');
  expect(screen.queryByText('tv runtime mounted')).toBeNull();
});
it('waits for an intentional Kite selection even with a broker deep link', async () => {
  window.history.replaceState({}, '', '/?broker_login=1');
  show({ ...base, features: { kite: true, tv: true, radio: true } });
  await screen.findByText('Welcome, Member.');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Open Echo/ }));
  await screen.findByText('radio runtime mounted');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
});
it('offers central administration even when the admin has no feature grants', async () => {
  show({ ...base, role: 'admin', administrator: true });
  await screen.findByText('Welcome, Member.');
  fireEvent.click(screen.getByRole('button', { name: 'Administration' }));
  await screen.findByText('Central administration mounted');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
});

const allFeatures = { ...base, features: { kite: true, tv: true, radio: true } };
it('returns from a workspace to the chooser through Settings without mounting another feature', async () => {
  show(allFeatures);
  fireEvent.click(await screen.findByRole('button', { name: /Open Echo/ }));
  await screen.findByText('radio runtime mounted');
  fireEvent.click(screen.getByText('Settings', { selector: 'summary' }));
  fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
  await screen.findByText('Choose your workspace.');
  expect(screen.queryByText(/runtime mounted/)).toBeNull();
  expect(window.location.pathname).toBe('/');
  fireEvent.click(screen.getByRole('button', { name: /Open Space/ }));
  await screen.findByText('tv runtime mounted');
});
it('switches directly through the Kite, Echo and Space text controls', async () => {
  show(allFeatures);
  fireEvent.click(await screen.findByRole('button', { name: /Open Kite/ }));
  await screen.findByText('Kite runtime mounted');
  fireEvent.click(screen.getByRole('button', { name: 'Echo' }));
  await screen.findByText('radio runtime mounted');
  expect(screen.queryByText('Kite runtime mounted')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Space' }));
  await screen.findByText('tv runtime mounted');
  expect(window.location.pathname).toBe('/tv');
});
it('keeps an unapplied settings draft when workspace switching is cancelled', async () => {
  show(allFeatures);
  fireEvent.click(await screen.findByRole('button', { name: /Open Kite/ }));
  await screen.findByText('Kite runtime mounted');
  setDraftDirty('workspace-test', true);
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: 'Echo' }));
  expect(screen.getByText('Kite runtime mounted')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/kite');
});

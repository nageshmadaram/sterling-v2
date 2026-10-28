import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SterlingUsersPanel } from '../SterlingUsersPanel';
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../utils/api', () => ({ api }));
vi.mock('../../../store/useViewStore', () => ({ useIsSimulation: () => false }));
function show() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><SterlingUsersPanel /></QueryClientProvider>); }
beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({ current_user_id: 'primary', users: [
    { id: 'primary', username: 'Sterling', primary: true, role: 'admin', active: true, password_set: true },
    { id: 'member', username: 'Member', primary: false, role: 'user', active: true, password_set: true },
  ] });
});
describe('Sterling users', () => {
  it('lists password status and protects the primary login', async () => {
    show(); await screen.findByText('Sterling');
    expect(screen.getByText('Admin')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.queryByText(/one-way hashes/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Member' }));
    expect(screen.getAllByRole('button', { name: 'Suspend login' })).toHaveLength(1);
    expect(api.post).not.toHaveBeenCalled();
  });
  it('requires matching passwords before adding a user', async () => {
    show(); fireEvent.click(await screen.findByRole('button', { name: /Add user/ }));
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'new-user' } });
    fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value: 'Synthetic-password-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save user' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Passwords do not match');
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Repeat password'), { target: { value: 'Synthetic-password-123' } });
    api.post.mockResolvedValue({ id: 'new' });
    fireEvent.click(screen.getByRole('button', { name: 'Save user' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/v1/auth/users', { username: 'new-user', password: 'Synthetic-password-123', role: 'user', active: true, features:{kite:false,radio:false,tv:false},visibility:{kite:true,radio:true,tv:true},configuration:{brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}} }));
  });
});

it('sets initial workspace and settings access in the creation request',async()=>{
  show();fireEvent.click(await screen.findByRole('button',{name:/Add user/}));
  fireEvent.change(screen.getByLabelText('Username'),{target:{value:'configured-user'}});
  for(const name of ['Password','Repeat password'])fireEvent.change(screen.getByLabelText(name,{exact:true}),{target:{value:'Synthetic-password-123'}});
  fireEvent.click(screen.getByLabelText('Allow radio access'));
  fireEvent.click(screen.getByLabelText('Broker settings'));
  fireEvent.click(screen.getByLabelText('Add connections'));
  api.post.mockResolvedValue({id:'configured'});
  fireEvent.click(screen.getByRole('button',{name:'Save user'}));
  await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/api/v1/auth/users',expect.objectContaining({features:{kite:false,radio:true,tv:false},configuration:expect.objectContaining({brokers:{visible:true,grants:[{action:'create',resource_id:'own',expires_at_ms:null}]}})})));
});

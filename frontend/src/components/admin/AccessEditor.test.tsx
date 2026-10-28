import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AccessEditor } from './AccessEditor';
import { resetDraftGuard } from '../kite/config/unsavedDraftGuard';

const api = vi.hoisted(() => ({get:vi.fn(),put:vi.fn()}));
vi.mock('../../utils/api',()=>({api}));

const policy = {user_id:'member',revision:4,
  features:{kite:false,radio:true,tv:true},
  visibility:{kite:true,radio:true,tv:true}};

beforeEach(()=>{
  vi.resetAllMocks();
  api.get.mockImplementation((path:string)=>path.endsWith('/features')
    ? Promise.resolve(policy) : Promise.reject(new Error('Fixture excludes settings sections')));
  api.put.mockResolvedValue({...policy,revision:5,visibility:{kite:false,radio:true,tv:true}});
});
afterEach(()=>{cleanup();resetDraftGuard();});

it('updates only Kite menu visibility while retaining feature grants',async()=>{
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><AccessEditor userId="member" /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('checkbox',{name:'Show kite in workspace chooser'}));
  fireEvent.click(screen.getByRole('button',{name:'Save workspace settings'}));
  await waitFor(()=>expect(api.put).toHaveBeenCalledTimes(1));
  expect(api.put).toHaveBeenCalledWith('/api/v1/auth/users/member/features',{
    expected_revision:4,
    features:{kite:false,radio:true,tv:true},
    visibility:{kite:false,radio:true,tv:true},
  });
});

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AccessEditor } from './AccessEditor';
import { resetDraftGuard } from '../kite/config/unsavedDraftGuard';

const api = vi.hoisted(() => ({get:vi.fn(),put:vi.fn()}));
vi.mock('../../utils/api',()=>({api}));

const configuration = {user_id:'member',revision:4,administrator:false,active:true,
  sections:{brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}}};
const policy = {user_id:'member',revision:4,
  features:{kite:false,radio:true,tv:true},
  visibility:{kite:true,radio:true,tv:true}};

beforeEach(()=>{
  vi.resetAllMocks();
  api.get.mockImplementation((path:string)=>path.endsWith('/features')
    ? Promise.resolve(policy) : path.endsWith('/configuration-access')
      ? Promise.resolve(configuration) : Promise.reject(new Error('Unexpected request')));
  api.put.mockResolvedValue({...policy,revision:5,visibility:{kite:false,radio:true,tv:true}});
});
afterEach(()=>{cleanup();resetDraftGuard();});

it('updates only Kite menu visibility while retaining feature grants',async()=>{
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><AccessEditor userId="member" /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('checkbox',{name:'Show kite in workspace chooser'}));
  const save = screen.getByRole('button',{name:'Save workspace settings'});
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByRole('combobox',{name:'Reason for workspace change'}),{target:{value:'ACCESS_REVIEW'}});
  fireEvent.click(save);
  await waitFor(()=>expect(api.put).toHaveBeenCalledTimes(1));
  expect(api.put).toHaveBeenCalledWith('/api/v1/auth/users/member/features',{
    expected_revision:4,
    features:{kite:false,radio:true,tv:true},
    visibility:{kite:false,radio:true,tv:true},
    reason_code:'ACCESS_REVIEW',
  });
});


it('requires reason code before granting broker configuration visibility',async()=>{
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  api.put.mockResolvedValue({...configuration,revision:5,sections:{
    ...configuration.sections,brokers:{visible:true,grants:[]},
  }});
  render(<QueryClientProvider client={client}><AccessEditor userId="member" /></QueryClientProvider>);
  await screen.findByRole('heading',{name:'Broker settings'});
  const save = screen.getAllByRole('button',{name:'Save permissions'})[0];
  expect(save).toBeDisabled();
  fireEvent.click(screen.getAllByRole('checkbox',{name:'Visible'})[0]);
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByRole('combobox',{name:'Reason for brokers settings change'}),{
    target:{value:'USER_REQUEST'},
  });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(()=>expect(api.put).toHaveBeenCalledWith('/api/v1/auth/users/member/configuration-access',{
    section:'brokers',visible:true,grants:[],expected_revision:4,reason_code:'USER_REQUEST',
  }));
});

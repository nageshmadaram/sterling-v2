import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import Administration from '../../../pages/Administration';
const api=vi.hoisted(()=>({get:vi.fn(),put:vi.fn(),patch:vi.fn()}));
vi.mock('../../../utils/api',()=>({api}));
const sections={brokers:{visible:false,grants:[]},telegram:{visible:false,grants:[]},integrations:{visible:false,grants:[]}};
const capabilities={administrator:true,active:true,revision:0,sections};
const member={id:'member',username:'Member',active:true,role:'user',features:{kite:false,radio:false,tv:false},revision:3,activity:[],brokers:[],telegram:[],online:false,last_login_ms:null};
beforeEach(()=>{vi.resetAllMocks();api.get.mockImplementation(async(url:string)=>{
 if(url==='/api/v1/settings/capabilities')return capabilities;
 if(url==='/api/v1/admin/directory')return {users:[member],brokers:[]};
 if(url==='/api/v1/auth/users')return {current_user_id:'admin',users:[member]};
 if(url==='/api/v1/admin/access-requests')return {requests:[]};
 if(url.endsWith('/features'))return {user_id:'member',revision:3,features:member.features};
 if(url.endsWith('/configuration-access'))return {...capabilities,administrator:false};
 throw Error(url);
});});
afterEach(cleanup);
function show(){render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><Administration/></QueryClientProvider>);}
it('shows access and connection ownership beside a selectable user, without a user combobox',async()=>{show();await screen.findByRole('heading',{name:'Member'});expect(screen.getByRole('combobox',{name:'Account status'})).toBeVisible();expect(screen.getByText('None assigned')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Activity'}));expect(await screen.findByText('No activity recorded.')).toBeVisible();expect(api.get.mock.calls.every(([url])=>!url.includes('/kite'))).toBe(true);});
it('submits a bulk grant with the observed policy revision',async()=>{api.patch.mockResolvedValue({updated:1});show();fireEvent.click(await screen.findByLabelText('Select Member'));fireEvent.click(screen.getAllByRole('button',{name:'Enable'})[0]);await waitFor(()=>expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/directory/access',{users:[{user_id:'member',expected_revision:3}],features:{kite:true}}));});
it('denies ordinary users before fetching the directory',async()=>{api.get.mockResolvedValue({...capabilities,administrator:false});show();await screen.findByRole('alert');expect(api.get).not.toHaveBeenCalledWith('/api/v1/admin/directory');});

it('opens creation with access controls directly from the directory',async()=>{show();fireEvent.click(await screen.findByRole('button',{name:'Add user'}));expect(await screen.findByRole('form',{name:'Create Sterling user'})).toBeVisible();expect(screen.getByLabelText('Allow tv access')).toBeVisible();expect(screen.getByLabelText('Telegram settings')).toBeVisible();});
it('filters users by name and exposes an empty search state',async()=>{show();fireEvent.change(await screen.findByRole('searchbox',{name:'Find a user'}),{target:{value:'no-such-member'}});expect(screen.queryByLabelText('Select Member')).toBeNull();expect(screen.getByText('No users match your search.')).toBeVisible();});

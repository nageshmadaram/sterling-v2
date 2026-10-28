import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {PushSettings} from '../PushSettings';
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn(),show:vi.fn(),registration:vi.fn()}));
vi.mock('../../../utils/api',()=>({api:{get:mocks.get,post:mocks.post}}));
beforeEach(()=>{
 vi.resetAllMocks();
 vi.stubGlobal('Notification',{permission:'granted'});
 vi.stubGlobal('PushManager',class {});
 vi.stubGlobal('isSecureContext',true);
 vi.stubGlobal('navigator',{serviceWorker:{getRegistration:mocks.registration}});
 mocks.get.mockResolvedValue({public_key:'test',config:{enabled:true,signals:true,pcr:true,alerts:true,quiet_enabled:false,quiet_start:'22:00',quiet_end:'08:00',max_per_hour:12},devices:[]});
 mocks.registration.mockResolvedValue({active:{scriptURL:new URL('/sw.js',window.location.origin).href},showNotification:mocks.show});
 mocks.show.mockResolvedValue(undefined);
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function mount(){render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><PushSettings/></QueryClientProvider>);}
it('checks device display independently of server push and uses a same-origin read-only destination',async()=>{
 mount();fireEvent.click(await screen.findByRole('button',{name:'Check notification display'}));
 await waitFor(()=>expect(mocks.show).toHaveBeenCalledWith('Sterling · device display check',expect.objectContaining({data:{url:new URL('/kite?view=live&notification=alerts',window.location.origin).href}})));
 expect(await screen.findByText(/The browser created the device check/)).toBeInTheDocument();
 expect(mocks.post).not.toHaveBeenCalled();
});
it('explains denied site permission without claiming notification delivery',async()=>{
 vi.stubGlobal('Notification',{permission:'denied'});mount();
 fireEvent.click(await screen.findByRole('button',{name:'Check notification display'}));
 expect(await screen.findByText('Allow notifications for this site, then enable this device.')).toBeInTheDocument();
 expect(mocks.show).not.toHaveBeenCalled();expect(mocks.post).not.toHaveBeenCalled();
});

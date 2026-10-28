import {afterEach,expect,it,vi} from 'vitest';
import {handleMediaHealth} from '../mediaHealthBridge';
import {api} from '../../../utils/api';
vi.mock('../../../utils/api',()=>({api:{get:vi.fn(),post:vi.fn()}}));
afterEach(()=>vi.clearAllMocks());
it('routes only to the authenticated workspace health endpoint and returns no credentials',async()=>{
 const frame={postMessage:vi.fn()} as unknown as Window;vi.mocked(api.get).mockResolvedValue({total:3,review:[]});
 expect(handleMediaHealth('radio',{type:'sterling.media.health',kind:'radio',requestId:'health-1',op:'list'},frame)).toBe(true);
 await vi.waitFor(()=>expect(frame.postMessage).toHaveBeenCalledWith({type:'sterling.media.health.result',requestId:'health-1',result:{total:3,review:[]}},'*'));
 expect(api.get).toHaveBeenCalledWith('/api/v1/media/radio/health');
});
it.each([{kind:'tv',op:'list'},{kind:'radio',op:'delete'},{kind:'radio',op:'sync',requestId:'../../private'},{kind:'radio',op:'sync',body:'a'.repeat(512001)}])('rejects mismatched or malformed messages before calling APIs',extra=>{
 const frame={postMessage:vi.fn()} as unknown as Window;
 handleMediaHealth('radio',{type:'sterling.media.health',requestId:'health-2',...extra},frame);
 expect(api.get).not.toHaveBeenCalled();expect(api.post).not.toHaveBeenCalled();
});
it('sends catalogue batches to a fixed route, not an iframe-selected URL',async()=>{
 const frame={postMessage:vi.fn()} as unknown as Window;vi.mocked(api.post).mockResolvedValue({saved:1});const body={stations:[{key:'x',name:'Station',sources:['https://example.com/x']}]};
 handleMediaHealth('tv',{type:'sterling.media.health',kind:'tv',requestId:'health-3',op:'sync',url:'/api/v1/orders',body},frame);
 expect(api.post).toHaveBeenCalledWith('/api/v1/media/tv/health/catalogue',body);
 await vi.waitFor(()=>expect(frame.postMessage).toHaveBeenCalled());
});

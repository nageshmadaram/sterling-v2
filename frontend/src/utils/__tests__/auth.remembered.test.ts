import {afterEach,expect,it,vi} from 'vitest';
import {AUTH_REQUEST_TIMEOUT_MS,clearAuthTokens,getRefreshToken,refreshSterlingAccess,setAuthTokens} from '../auth';
afterEach(()=>{vi.restoreAllMocks();vi.useRealTimers();clearAuthTokens();});
it('retains the saved sign-in during a server outage',async()=>{
 setAuthTokens({access_token:'test-access',refresh_token:'test-refresh'});
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{}',{status:503}));
 expect(await refreshSterlingAccess()).toBe(false);expect(getRefreshToken()).toBe('test-refresh');
});
it('shares concurrent renewals and clears only rejected credentials',async()=>{
 setAuthTokens({access_token:'test-access',refresh_token:'test-refresh'});
 const request=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response('{}',{status:401}));
 await Promise.all([refreshSterlingAccess(),refreshSterlingAccess()]);
 expect(request).toHaveBeenCalledTimes(1);expect(getRefreshToken()).toBe('');
});
it('bounds a suspended mobile refresh and allows a later retry without losing the session',async()=>{
 vi.useFakeTimers();
 setAuthTokens({access_token:'test-access',refresh_token:'test-refresh'});
 const request=vi.spyOn(globalThis,'fetch').mockImplementation((_input,options)=>new Promise((_resolve,reject)=>{
  options?.signal?.addEventListener('abort',()=>reject(new DOMException('Timed out','AbortError')));
 }));
 const pending=refreshSterlingAccess();
 await vi.advanceTimersByTimeAsync(AUTH_REQUEST_TIMEOUT_MS);
 expect(await pending).toBe(false);expect(getRefreshToken()).toBe('test-refresh');
 request.mockResolvedValue(new Response(JSON.stringify({access_token:'renewed',refresh_token:'renewed-refresh'}),{status:200}));
 expect(await refreshSterlingAccess()).toBe(true);expect(getRefreshToken()).toBe('renewed-refresh');
});

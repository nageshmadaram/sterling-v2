import {afterEach,expect,it,vi} from 'vitest';
import {clearAuthTokens,getRefreshToken,refreshSterlingAccess,setAuthTokens} from '../auth';
afterEach(()=>{vi.restoreAllMocks();clearAuthTokens();});
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

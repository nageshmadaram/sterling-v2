import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
const get=vi.fn(),patch=vi.fn();
vi.mock('../utils/api',()=>({api:{get:(...args:unknown[])=>get(...args),patch:(...args:unknown[])=>patch(...args)}}));
import { startPreferenceSync,stopPreferenceSync,flushPreferences,usePreferenceSync } from './preferences';
import { useKiteSettings } from '../store/useKiteSettings';
import { isPreferenceKey } from './preferenceKeys';
beforeEach(()=>{stopPreferenceSync();localStorage.clear();get.mockReset();patch.mockReset();patch.mockResolvedValue({values:{},initialized:true});});
afterEach(()=>stopPreferenceSync());
describe('Database preferences',()=>{
 it('excludes credentials and broker protection state',()=>{expect(isPreferenceKey('sterling.auth.access')).toBe(false);expect(isPreferenceKey('sterling.kite.pending-protection.v1')).toBe(false);expect(isPreferenceKey('sterling_appearance_v1')).toBe(true);});
 it('hydrates authoritative settings before changes and patches only changed keys',async()=>{
 const saved=JSON.stringify({version:8,state:{defaultSignalEngine:'snapback'}});
 get.mockResolvedValue({values:{'kite-settings':saved,sterling_theme:'dark'},initialized:true});
 await startPreferenceSync('alice');
 expect(useKiteSettings.getState().defaultSignalEngine).toBe('snapback');
 await flushPreferences();patch.mockClear();localStorage.setItem('sterling_theme','light');await flushPreferences();
 expect(patch).toHaveBeenLastCalledWith('/api/v1/auth/preferences',{values:{sterling_theme:'light'}});
 });
 it('retains failed saves and retries them',async()=>{
 get.mockResolvedValue({values:{},initialized:true});await startPreferenceSync('alice');await flushPreferences();
 patch.mockRejectedValueOnce(new Error('offline'));localStorage.setItem('sterling_theme','dark');await flushPreferences();expect(usePreferenceSync.getState().state).toBe('error');
 expect(localStorage.getItem('sterling.preferences.pending.v1.alice')).toContain('dark');
 await flushPreferences();expect(usePreferenceSync.getState().state).toBe('saved');
 });
 it('does not migrate another operators cached settings',async()=>{
 localStorage.setItem('sterling.preferences.owner.v1','alice');localStorage.setItem('sterling_theme','dark');get.mockResolvedValue({values:{},initialized:false});
 await startPreferenceSync('bob');expect(patch.mock.calls[0][1]).toEqual({values:{},initialize_only:true});expect(localStorage.getItem('sterling_theme')).toBe('light');
 });
});

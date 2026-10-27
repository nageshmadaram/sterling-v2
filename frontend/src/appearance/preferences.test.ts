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
 it('saves media changes and clears them when another user signs in',async()=>{
 const key='sterling.radio.omnicast_v11_config';
 get.mockResolvedValue({values:{[key]:'{"startupStationId":"alice-fm"}'},initialized:true});
 await startPreferenceSync('alice');await flushPreferences();patch.mockClear();
 localStorage.setItem(key,'{"startupStationId":"new-fm"}');await flushPreferences();
 expect(patch).toHaveBeenLastCalledWith('/api/v1/auth/preferences',{values:{[key]:'{"startupStationId":"new-fm"}'}});
 get.mockResolvedValue({values:{},initialized:true});await startPreferenceSync('bob');
 expect(localStorage.getItem(key)).toBeNull();
 });

 it('migrates existing media settings when an account already has server preferences',async()=>{
 const key='sterling.tv.streamos_startup_channel_v14';
 localStorage.setItem('sterling.preferences.owner.v1','alice');localStorage.setItem(key,'{"name":"My TV"}');
 get.mockResolvedValue({values:{sterling_theme:'dark'},initialized:true});
 patch.mockImplementation(async(_url,body)=>({values:{sterling_theme:'dark',...body.values},initialized:true}));
 await startPreferenceSync('alice');
 expect(patch).toHaveBeenCalledWith('/api/v1/auth/preferences',{values:{[key]:'{"name":"My TV"}'}});
 expect(localStorage.getItem(key)).toBe('{"name":"My TV"}');
 });

});

it('does not revive unowned legacy TV even for the recorded browser owner',async()=>{
 const legacy='sterling.tv.streamos_favs_v13',key='sterling.tv.streamos_favs_v14';
 localStorage.setItem('sterling.preferences.owner.v1','alice');localStorage.setItem(legacy,'[]');
 get.mockResolvedValue({values:{},initialized:true});patch.mockImplementation(async(_url,body)=>({values:body.values,initialized:true}));
 await startPreferenceSync('alice');expect(localStorage.getItem(key)).toBeNull();expect(localStorage.getItem(legacy)).toBe('[]');
 get.mockResolvedValue({values:{[key]:null},initialized:true});await startPreferenceSync('alice');expect(localStorage.getItem(key)).toBeNull();
 get.mockResolvedValue({values:{},initialized:true});await startPreferenceSync('bob');expect(localStorage.getItem(key)).toBeNull();
});

it('does not stall first sync on an oversized Unicode media cache',async()=>{
 const key='sterling.tv.streamos_favs_v14';localStorage.setItem('sterling.preferences.owner.v1','alice');localStorage.setItem(key,'త'.repeat(170000));
 get.mockResolvedValue({values:{},initialized:false});patch.mockImplementation(async(_url,body)=>({values:body.values,initialized:true}));
 await startPreferenceSync('alice');expect(patch.mock.calls.every(call=>call[1].values[key]===undefined)).toBe(true);expect(localStorage.getItem(key)).toBeNull();
});

it('saves caption appearance for its owner and clears it on an account change',async()=>{
 const key='sterling.tv.streamos_sub_style_v14';const saved=JSON.stringify({size:125,color:'#ffcc00',bg:0.7});
 get.mockResolvedValue({values:{[key]:saved},initialized:true});
 await startPreferenceSync('alice');await flushPreferences();
 expect(localStorage.getItem(key)).toBe(saved);patch.mockClear();
 const changed=JSON.stringify({size:150,color:'#ffffff',bg:0.5});
 localStorage.setItem(key,changed);await flushPreferences();
 expect(patch).toHaveBeenLastCalledWith('/api/v1/auth/preferences',{values:{[key]:changed}});
 get.mockResolvedValue({values:{},initialized:true});await startPreferenceSync('bob');
 expect(localStorage.getItem(key)).toBeNull();
});

it('initializes a fresh account using only supported preference keys',async()=>{
 localStorage.setItem('sterling_theme','light');
 localStorage.setItem('sterling_ui_state','{"theme":"dark","routerMode":"live"}');
 get.mockResolvedValue({values:{},initialized:false});
 patch.mockImplementation(async(_url,body)=>{
  if(Object.keys(body.values).some(key=>!isPreferenceKey(key))) throw new Error('422 Unsupported preference key');
  return {values:body.values,initialized:true};
 });
 await expect(startPreferenceSync('new-radio-user')).resolves.toBeUndefined();
 expect(patch).toHaveBeenCalledWith('/api/v1/auth/preferences',{values:{sterling_theme:'light'},initialize_only:true});
 expect(patch.mock.calls.every(call=>!('sterling_ui_state' in call[1].values))).toBe(true);
 expect(usePreferenceSync.getState().state).not.toBe('error');
});

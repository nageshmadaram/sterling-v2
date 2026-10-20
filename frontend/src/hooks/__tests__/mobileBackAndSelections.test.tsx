import React,{useState} from 'react';
import {act,cleanup,fireEvent,render,renderHook} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {useMobileBackLayer} from '../useMobileBackLayer';
import {useSavedSelection} from '../useSavedSelection';
import {isPreferenceKey} from '../../appearance/preferenceKeys';
afterEach(()=>{cleanup();vi.unstubAllGlobals();sessionStorage.clear();localStorage.clear();history.replaceState(null,'');});
it('mobile Back dismisses the top layer',()=>{
 vi.stubGlobal('innerWidth',390);const closed=vi.fn();
 function Layer(){const [open,setOpen]=useState(true);useMobileBackLayer(open,()=>{closed();setOpen(false);},'test-overlay');return open?<div role="dialog">Overlay</div>:<p>Closed</p>;}
 const screen=render(<Layer/>);
 expect(history.state.sterlingLayer).toContain('test-overlay');
 act(()=>{history.replaceState(null,'');fireEvent.popState(window);});
 expect(closed).toHaveBeenCalledOnce();expect(screen.queryByRole('dialog')).toBeNull();
});
it('restores only page navigation in this browser session',()=>{
 const first=renderHook(()=>useSavedSelection<string>('workspace.nav','dashboard'));
 act(()=>first.result.current[1]('pcr'));first.unmount();
 expect(renderHook(()=>useSavedSelection<string>('workspace.nav','dashboard')).result.current[0]).toBe('pcr');
 expect(isPreferenceKey('sterling.selection.workspace.nav')).toBe(false);
 expect(isPreferenceKey('sterling.experience.options.v1')).toBe(true);
 const filter=renderHook(()=>useSavedSelection<string>('board.query',''));
 act(()=>filter.result.current[1]('SBIN'));filter.unmount();
 expect(renderHook(()=>useSavedSelection<string>('board.query','')).result.current[0]).toBe('');
});
it('removes a dismissed lower layer from the replacement overlay history',()=>{
 vi.stubGlobal('innerWidth',390);
 const lower=renderHook(({open})=>useMobileBackLayer(open,()=>{},'search'),{initialProps:{open:true}});
 const upper=renderHook(()=>useMobileBackLayer(true,()=>{},'ticket'));
 lower.rerender({open:false});upper.unmount();
 expect(history.state?.sterlingLayer).toBeUndefined();lower.unmount();
});

it('Home dismisses every nested mobile overlay without invoking Back',()=>{
 vi.stubGlobal('innerWidth',390);
 const closeLower=vi.fn(),closeUpper=vi.fn();
 function Layer({id,onClose}:{id:string;onClose:()=>void}) {
   useMobileBackLayer(true,onClose,id);
   return <div role="dialog">{id}</div>;
 }
 const rendered=render(<><Layer id="search" onClose={closeLower}/><Layer id="order" onClose={closeUpper}/></>);
 act(()=>window.dispatchEvent(new Event('sterling-home')));
 expect(closeLower).toHaveBeenCalledOnce();
 expect(closeUpper).toHaveBeenCalledOnce();
 rendered.unmount();
});

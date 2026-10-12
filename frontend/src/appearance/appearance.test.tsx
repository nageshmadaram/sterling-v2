import React from 'react';
import { describe,it,expect,beforeEach } from 'vitest';
import { render,screen,fireEvent,cleanup } from '@testing-library/react';
import { THEME_PRESETS } from './catalog';
import { applyAppearance,validateAppearance,DEFAULT_APPEARANCE,useAppearance,APPEARANCE_KEY } from './store';
import { AppearanceSettings } from '../components/kite/AppearanceSettings';
import { useStore } from '../store/useStore';
beforeEach(()=>{cleanup();localStorage.clear();useAppearance.getState().replace(DEFAULT_APPEARANCE);useStore.getState().setTheme('light');});
describe('Appearance packages',()=>{
 it('provides more than 50 unique complete themes',()=>{expect(THEME_PRESETS.length).toBeGreaterThan(50);expect(new Set(THEME_PRESETS.map(p=>p.id)).size).toBe(THEME_PRESETS.length);for(const p of THEME_PRESETS)for(const c of [p.bg,p.surface,p.text,p.accent])expect(c).toMatch(/^#[0-9a-f]{6}$/i);});
 it('rejects injected import values',()=>{for(const v of [{accent:'url(https://example.com)'},{preset:'unknown'},{motion:'spin'},null])expect(()=>validateAppearance(v)).toThrow();});
 it('restores stock tokens and preserves trading semantic colours',()=>{const root=document.documentElement;applyAppearance({...DEFAULT_APPEARANCE,preset:'matrix'},'dark');expect(root.style.getPropertyValue('--k-bg')).toBe(THEME_PRESETS.find(p=>p.id==='matrix')!.surface);expect(root.style.getPropertyValue('--k-green')).toBe('');expect(root.style.getPropertyValue('--k-red')).toBe('');applyAppearance(DEFAULT_APPEARANCE,'light');expect(root.style.getPropertyValue('--k-bg')).toBe('');});
 it('preview does not apply; Apply persists',()=>{render(<AppearanceSettings/>);fireEvent.click(screen.getByRole('button',{name:'Preview Matrix'}));expect(screen.getByRole('dialog',{name:'Matrix preview'})).toBeInTheDocument();expect(useAppearance.getState().settings.preset).toBe('');fireEvent.click(screen.getByRole('button',{name:'Cancel'}));fireEvent.click(screen.getByRole('button',{name:'Apply Matrix'}));expect(useStore.getState().theme).toBe('dark');expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!).preset).toBe('matrix');});
 it('searches families and saves favourites',()=>{render(<AppearanceSettings/>);fireEvent.change(screen.getByRole('textbox',{name:'Search themes'}),{target:{value:'Ubuntu'}});expect(screen.queryByRole('button',{name:'Apply Matrix'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'Favourite Aubergine'}));expect(useAppearance.getState().settings.favorites).toEqual(['aubergine']);});
 it('imports a valid package and restores defaults',()=>{render(<AppearanceSettings/>);fireEvent.click(screen.getByText('Save, share & restore appearance'));fireEvent.change(screen.getByRole('textbox',{name:'Appearance package'}),{target:{value:JSON.stringify({...DEFAULT_APPEARANCE,preset:'matrix',base:'dark',motion:'off'})}});fireEvent.click(screen.getByRole('button',{name:'Import package'}));expect(useAppearance.getState().settings.motion).toBe('off');fireEvent.click(screen.getByRole('button',{name:'Restore defaults'}));expect(useStore.getState().theme).toBe('light');expect(useAppearance.getState().settings.preset).toBe('');});
});

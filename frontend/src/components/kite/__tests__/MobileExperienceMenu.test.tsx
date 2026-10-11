import React from 'react';
import {beforeEach,describe,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {MobileExperienceMenu} from '../MobileExperienceMenu';
import {useMobileExperience,MOBILE_STYLE_KEY} from '../../../store/useMobileExperience';
import {useAppInstall} from '../../../store/useAppInstall';
vi.mock('../config/unsavedDraftGuard',()=>({hasUnsavedDraft:()=>false}));
beforeEach(()=>{cleanup();localStorage.clear();useMobileExperience.setState({style:'app'});useAppInstall.setState({prompt:null,installed:false});});
describe('mobile app menu',()=>{
 it('persists layout choice and offers every page',()=>{
  const nav=vi.fn();render(<MobileExperienceMenu activeNav="dashboard" onNavClick={nav}/>);
  fireEvent.click(screen.getByRole('button',{name:/Menu/}));
  expect(screen.getAllByRole('button').length).toBeGreaterThan(12);
  fireEvent.click(screen.getByRole('button',{name:'Full app style'}));
  expect(localStorage.getItem(MOBILE_STYLE_KEY)).toBe('full');
  fireEvent.click(screen.getByRole('button',{name:/Positions Exposure/}));
  expect(nav).toHaveBeenCalledWith('positions');expect(screen.queryByRole('dialog')).toBeNull();
 });
 it('provides instructions when the browser has no install prompt',()=>{
  render(<MobileExperienceMenu activeNav="dashboard" onNavClick={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:/Menu/}));fireEvent.click(screen.getByRole('button',{name:'Install / add to home screen'}));
  expect(screen.getByRole('status').textContent).toContain('Chrome or Brave');expect(screen.getByRole('status').textContent).toContain('Safari');
 });
 it('only prompts installation after a user gesture',async()=>{
  const prompt=vi.fn().mockResolvedValue(undefined);useAppInstall.setState({prompt:{prompt,userChoice:Promise.resolve({outcome:'accepted'})} as any});
  render(<MobileExperienceMenu activeNav="dashboard" onNavClick={vi.fn()}/>);expect(prompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:/Menu/}));fireEvent.click(screen.getByRole('button',{name:'Install Sterling'}));
  await waitFor(()=>expect(screen.getByRole('status').textContent).toContain('Installation requested'));expect(prompt).toHaveBeenCalledTimes(1);
 });
});

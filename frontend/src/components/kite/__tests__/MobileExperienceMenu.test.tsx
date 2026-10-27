import React from 'react';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MobileExperienceMenu } from '../MobileExperienceMenu';
import { useMobileExperience, MOBILE_STYLE_KEY } from '../../../store/useMobileExperience';
import { useAppInstall } from '../../../store/useAppInstall';
import { readExperienceOptions, useExperienceOptions } from '../../../store/useExperienceOptions';
vi.mock('../ThemeToggle',()=>({ThemeToggle:()=> <button aria-label="Choose theme">Theme</button>}));
vi.mock('../config/unsavedDraftGuard',()=>({hasUnsavedDraft:()=>false}));
beforeEach(()=>{
  cleanup(); localStorage.clear();
  useMobileExperience.setState({style:'app'});
  useExperienceOptions.setState({options:readExperienceOptions()});
  useAppInstall.setState({prompt:null,installed:false});
});
afterEach(()=>cleanup());
const mount=(nav=vi.fn(),onSearch=vi.fn()) => {
  render(<><div id="sterling-mobile-header-actions" /><MobileExperienceMenu
    activeNav="dashboard" onNavClick={nav} onSearch={onSearch}
    tools={[{id:'orders',label:'Orders'}]} /></>);
  return {nav,onSearch};
};
describe('mobile Spectrum header and drawer',()=>{
  it('portals search and hamburger into the main header, not a second row',()=>{
    const {onSearch}=mount();
    expect(document.querySelector('.mobile-experience-bar')).toBeNull();
    const header=document.getElementById('sterling-mobile-header-actions')!;
    expect(header.querySelectorAll('button')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button',{name:'Search watchlist'}));
    expect(onSearch).toHaveBeenCalledOnce();
  });
  it('has visible close and theme controls and closes by X, backdrop and Escape',()=>{
    mount();
    const open=()=>fireEvent.click(screen.getByRole('button',{name:'Menu'}));
    open();
    expect(screen.getByRole('dialog',{name:'Sterling mobile menu'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Choose theme'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Close mobile menu'}));
    expect(screen.queryByRole('dialog')).toBeNull();
    open();
    fireEvent.click(document.querySelector('.mobile-experience-backdrop')!);
    expect(screen.queryByRole('dialog')).toBeNull();
    open();
    fireEvent.keyDown(window,{key:'Escape'});
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('moves Tools into the drawer, persists layout and closes after navigation',()=>{
    const {nav}=mount();
    fireEvent.click(screen.getByRole('button',{name:'Menu'}));
    expect(screen.getByRole('navigation',{name:'Tools'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Full app style'}));
    expect(localStorage.getItem(MOBILE_STYLE_KEY)).toBe('full');
    fireEvent.click(screen.getByRole('button',{name:/Positions Exposure/}));
    expect(nav).toHaveBeenCalledWith('positions');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('closes drawer when Home is activated and retains keyboard focus',()=>{
    mount();
    fireEvent.click(screen.getByRole('button',{name:'Menu'}));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent(window,new Event('sterling-home'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('keeps browser installation instructions available',()=>{
    mount();
    fireEvent.click(screen.getByRole('button',{name:'Menu'}));
    fireEvent.click(screen.getByRole('button',{name:'Install / add to home screen'}));
    expect(screen.getByRole('status')).toHaveTextContent('Chrome or Brave');
  });
});

it('lists each tool once rather than repeating it under app pages',()=>{
  render(<><div id="sterling-mobile-header-actions"/><MobileExperienceMenu activeNav="dashboard" onNavClick={vi.fn()} tools={[{id:'astro',label:'Astrology'}]}/></>);
  fireEvent.click(screen.getByRole('button',{name:'Menu'}));
  expect(screen.getAllByRole('button',{name:'Astrology'})).toHaveLength(1);
  expect(screen.getByRole('navigation',{name:'All app pages'})).not.toHaveTextContent('Astrology');
});

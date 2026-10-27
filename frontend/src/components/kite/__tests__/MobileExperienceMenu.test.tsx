import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {MobileExperienceMenu} from '../MobileExperienceMenu';
import {readExperienceOptions,useExperienceOptions} from '../../../store/useExperienceOptions';
vi.mock('../ProfileMenu',()=>({useSterlingProfile:()=>({data:{username:'Member',photo:''}})}));
beforeEach(()=>{localStorage.clear();useExperienceOptions.setState({options:readExperienceOptions()});HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};});
afterEach(cleanup);
it('places account and navigation in the same header menu, with hidden tools omitted',()=>{
  const options=useExperienceOptions.getState().options;useExperienceOptions.setState({options:{...options,navHidden:['astro']}});
  const navigate=vi.fn();
  render(<><div id="sterling-mobile-header-actions"/><MobileExperienceMenu activeNav="dashboard" onNavClick={navigate} tools={[{id:'astro',label:'Astrology'},{id:'orders',label:'Orders'}]}/></>);
  expect(document.getElementById('sterling-mobile-header-actions')?.querySelector('.account-menu-trigger')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Menu'}));
  expect(screen.queryByRole('button',{name:'Astrology'})).toBeNull();
  expect(screen.getAllByRole('button',{name:'Orders'})).toHaveLength(1);
  expect(screen.getByRole('button',{name:'Profile & security'})).toBeVisible();
  expect(screen.getByRole('button',{name:'☀ Light'})).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Orders'}));
  expect(navigate).toHaveBeenCalledWith('orders');
  expect(screen.queryByRole('dialog')).toBeNull();
});
it('closes the menu when Home is activated',()=>{
  render(<><div id="sterling-mobile-header-actions"/><MobileExperienceMenu activeNav="dashboard" onNavClick={vi.fn()}/></>);
  fireEvent.click(screen.getByRole('button',{name:'Menu'}));
  fireEvent(window,new Event('sterling-home'));
  expect(screen.queryByRole('dialog')).toBeNull();
});

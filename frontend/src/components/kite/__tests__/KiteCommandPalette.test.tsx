import {afterEach,beforeEach,it,expect,vi} from 'vitest';
import {render,fireEvent,screen,cleanup} from '@testing-library/react';
import {KiteCommandPalette} from '../KiteCommandPalette';
import {readExperienceOptions,useExperienceOptions} from '../../../store/useExperienceOptions';
beforeEach(()=>{localStorage.clear();useExperienceOptions.setState({options:{...readExperienceOptions(),navHidden:['astro']}});});
afterEach(cleanup);
it('cannot expose a hidden destination through command search',()=>{
  const navigate=vi.fn();render(<KiteCommandPalette open onClose={vi.fn()} onNavClick={navigate}/>);
  const search=screen.getByRole('textbox');fireEvent.change(search,{target:{value:'Astrology'}});
  expect(screen.queryByText('Astrology',{exact:true})).toBeNull();
  fireEvent.change(search,{target:{value:'Orders'}});fireEvent.keyDown(search,{key:'Enter'});
  expect(navigate).toHaveBeenCalledWith('orders');
});

it('closes on Escape before delayed input focus and offers a touch close control',()=>{
  const close=vi.fn();render(<KiteCommandPalette open onClose={close} onNavClick={vi.fn()}/>);
  fireEvent.keyDown(document,{key:'Escape'});expect(close).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'Close command palette'}));expect(close).toHaveBeenCalledTimes(2);
});

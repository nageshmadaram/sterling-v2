import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {UserCombobox} from '../UserCombobox';
afterEach(cleanup);
const users=[{id:'one',username:'Sterling',role:'user' as const,active:true,password_set:true,primary:false},{id:'two',username:'Viewer',role:'user' as const,active:true,password_set:true,primary:false}];
it('filters in one searchable picker and selects with the keyboard',()=>{
  const change=vi.fn();render(<UserCombobox users={users} value="" onChange={change} label="Account"/>);
  const input=screen.getByRole('combobox',{name:'Account'});
  fireEvent.focus(input);fireEvent.change(input,{target:{value:'view'}});
  expect(screen.getByRole('option',{name:/Viewer/})).toBeInTheDocument();
  expect(screen.queryByText('Sterling',{selector:'strong'})).toBeNull();
  fireEvent.keyDown(input,{key:'Enter'});expect(change).toHaveBeenCalledWith('two');
});
it('reports an empty search and closes with Escape',()=>{
  render(<UserCombobox users={users} value="one" onChange={vi.fn()} label="Account"/>);
  const input=screen.getByRole('combobox',{name:'Account'});fireEvent.focus(input);fireEvent.change(input,{target:{value:'missing'}});
  expect(screen.getByText(/No users match/)).toBeInTheDocument();fireEvent.keyDown(input,{key:'Escape'});
  expect(screen.queryByRole('listbox')).toBeNull();expect(input).toHaveValue('Sterling');
});

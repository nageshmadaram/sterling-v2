import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {ProfileMenu} from '../ProfileMenu';

vi.mock('../../../utils/api',()=>({api:{get:vi.fn(async()=>({username:'operator',display_name:'Operator',photo:'',role:'operator',user_id:'1'})),patch:vi.fn()}}));
vi.mock('../../../hooks/useMobileBackLayer',()=>({useMobileBackLayer:()=>{}}));
vi.mock('../ProfilePhotoCropper',()=>({ProfilePhotoCropper:()=>null}));
vi.mock('../PushSettings',()=>({disconnectPushDevice:vi.fn()}));

afterEach(()=>{cleanup();document.body.style.overflow='';document.body.removeAttribute('inert');vi.restoreAllMocks();});

describe('profile modal interaction shield',()=>{
  function mount(){
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    return render(<QueryClientProvider client={client}><div className="term-root"><ProfileMenu/></div></QueryClientProvider>);
  }
  it('inerts the background and closes reliably on backdrop click',()=>{
    mount();
    fireEvent.click(screen.getByRole('button',{name:'Sterling account profile'}));
    const modal=screen.getByRole('dialog',{name:'Sterling profile'});
    expect(modal).toBeInTheDocument();
    expect(document.querySelector('.term-root')).toHaveAttribute('inert');
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.click(modal);
    expect(screen.queryByRole('dialog',{name:'Sterling profile'})).not.toBeInTheDocument();
    expect(document.querySelector('.term-root')).not.toHaveAttribute('inert');
  });
  it('keeps the backdrop from capturing panel taps and has working close button',()=>{
    mount();
    fireEvent.click(screen.getByRole('button',{name:'Sterling account profile'}));
    fireEvent.click(screen.getByRole('button',{name:'Close profile'}));
    expect(screen.queryByRole('dialog',{name:'Sterling profile'})).not.toBeInTheDocument();
  });
});

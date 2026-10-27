import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {fireEvent,render,screen,cleanup} from '@testing-library/react';
import {KiteSessionBar} from '../KiteSessionBar';
import {useKiteStatusCardStore} from '../../../../store/useKiteStatusCardStore';
let status:any;
vi.mock('../../../../hooks/useKite',()=>({useKiteStatus:()=>status}));
beforeEach(()=>{status={data:{connected:true},isError:false};useKiteStatusCardStore.setState({isOpen:false});});
afterEach(cleanup);
function show(){render(<><span id="kite-session-status"/><KiteSessionBar/></>);}
it('places connection truth in the header without another full-width row',()=>{show();expect(screen.getByRole('button',{name:'Kite connected. Open connection'})).toBeVisible();expect(screen.queryByRole('alert')).toBeNull();});
it('never presents a stale connected value as verified',()=>{status.isError=true;show();expect(screen.getByRole('button',{name:'Kite unverified. Open connection'})).toBeVisible();});
it('opens details only after an explicit action',()=>{status.data.connected=false;show();fireEvent.click(screen.getByRole('button',{name:'Kite disconnected. Open connection'}));expect(useKiteStatusCardStore.getState().isOpen).toBe(true);});

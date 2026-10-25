import React from 'react';
import {beforeEach,expect,it,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {useOrderWindowStore} from '../../../../store/useOrderWindowStore';
import {BoardTicket} from '../BoardTicket';
let current: number|null = 120;
const calc=vi.fn();
vi.mock('../../../../hooks/useKite',()=>({useKiteQuote:()=>({data:{'NFO:TEST':{last_price:current}}})}));
vi.mock('../../AdaptiveEdgePositionCalculator',()=>({AdaptiveEdgePositionCalculator:(props:any)=>{calc(props);return <div/>;}}));
vi.mock('../../SterlingWatchList',()=>({QuoteDetail:({onBuy}:any)=><button disabled={!onBuy} onClick={onBuy}>Quote Buy</button>}));
function signal(over:any={}):any{return {id:'plan',engine:'supertrend',status:'armed',planPriced:true,executionEligible:true,instrument:{quoteKey:'NFO:TEST',symbol:'TEST',exchange:'NFO',lotSize:50},levels:{entry:100,ltp:110,stop:80,trail:null,target:150},sizing:{lots:1,quantity:50},sections:[],...over};}
beforeEach(()=>{current=120;calc.mockClear();useOrderWindowStore.setState({isOpen:false,options:null});});
it('shows the observed distance without changing the canonical entry or inventing a tolerance',()=>{
 render(<BoardTicket signal={signal()}/>);
 expect(screen.getByText(/Observed premium vs plan/)).toHaveTextContent(/\+20.00%/);
 expect(calc.mock.calls[0][0].defaultEntryPrice).toBe(100);
 expect(screen.getByText(/server rechecks/i)).toBeInTheDocument();
});
it('blocks the expanded generic Buy if its fresher premium reaches the plan stop',()=>{
 current=75;render(<BoardTicket signal={signal()}/>);
 expect(screen.getByRole('alert')).toHaveTextContent(/at or below.*stop/i);
 expect(calc.mock.calls[0][0].allowOrder).toBe(false);
 expect(screen.getByRole('button',{name:'Quote Buy'})).toBeDisabled();
});
it('does not replace an invalid current price with the earlier premium',()=>{
 current=Number.NaN;render(<BoardTicket signal={signal()}/>);
 expect(screen.getByText(/Observed premium vs plan/)).toHaveTextContent(/unavailable/i);
 expect(calc.mock.calls[0][0].allowOrder).toBe(false);
});
it('keeps Adaptive Edge detail practice-only as well as hiding its row action',()=>{
 render(<BoardTicket signal={signal({engine:'adaptive_edge'})}/>);
 expect(calc.mock.calls[0][0].allowOrder).toBe(false);
 expect(screen.getByRole('button',{name:'Quote Buy'})).toBeDisabled();
});

it('keeps signal protection metadata when opening the expanded quote Buy',()=>{
 render(<BoardTicket signal={signal()} tag="SUPER_TREND"/>);
 fireEvent.click(screen.getByRole('button',{name:'Quote Buy'}));
 expect(useOrderWindowStore.getState().options).toMatchObject({symbol:'TEST',fromSignal:true,planStop:80,lastPrice:100,initialQty:50});
});
it('passes the canonical stop separately from editable calculator sizing',()=>{
 render(<BoardTicket signal={signal()}/>);
 expect(calc.mock.calls[0][0].signalPlanStop).toBe(80);
});
it('retains missing-stop provenance so the order window can reject it',()=>{
 render(<BoardTicket signal={signal({levels:{entry:100,ltp:110,stop:null,trail:null,target:null}})}/>);
 fireEvent.click(screen.getByRole('button',{name:'Quote Buy'}));
 expect(useOrderWindowStore.getState().options).toMatchObject({fromSignal:true,planStop:null});
});

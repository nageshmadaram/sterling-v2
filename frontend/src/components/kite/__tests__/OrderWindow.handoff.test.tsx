import React from 'react';
import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import {act,fireEvent,render,screen} from '@testing-library/react';
import {OrderWindow} from '../OrderWindow';
import type {OrderWindowOptions} from '../../../store/useOrderWindowStore';
const mocks=vi.hoisted(()=>({place:vi.fn(),margin:vi.fn(),charges:vi.fn(),quotes:{},instruments:[] as any[]}));
vi.mock('../../../hooks/useKite',()=>({
 usePlaceKiteOrder:()=>({mutate:mocks.place,isPending:false}),
 useKiteOrderMargins:()=>({mutate:mocks.margin,isPending:false}),
 useKiteOrderCharges:()=>({mutate:mocks.charges}),
 useKiteMargins:()=>({data:{},refetch:vi.fn()}),
 useKiteInstrumentSearch:()=>({data:{instruments:mocks.instruments}}),
 useKiteQuote:()=>({data:mocks.quotes}),
}));
vi.mock('../../../hooks/useSterlingKiteEngine',()=>({useEngineActivity:()=>({data:{market_open:true}})}));
vi.mock('../../../hooks/useMobileBackLayer',()=>({useMobileBackLayer:()=>{}}));
vi.mock('../../../hooks/useWorkspaceOverlayBounds',()=>({useWorkspaceOverlayBounds:()=>({top:52,bottom:80})}));
const close=vi.fn();
beforeEach(()=>{vi.clearAllMocks();mocks.instruments=[];});
afterEach(()=>vi.useRealTimers());
it('replaces the same contract ticket with the newly selected signal price and quantity',()=>{
 const options:OrderWindowOptions={symbol:'TESTCE',exchange:'NFO',initialSide:'BUY',initialQty:50,lotSize:50,lastPrice:100,fromSignal:true,planStop:80};
 const view=render(<OrderWindow options={options} onClose={close}/>);
 expect(screen.getByRole('button',{name:/^Buy$/})).toBeEnabled();
 view.rerender(<OrderWindow options={{...options,lastPrice:150,initialQty:100,planStop:120}} onClose={close}/>);
 const buy=screen.getByRole('button',{name:/^Buy$/});
 expect(buy).toBeEnabled();
 fireEvent.click(buy);
 expect(mocks.place.mock.calls[0][0]).toMatchObject({tradingsymbol:'TESTCE',price:150,quantity:100,order_type:'LIMIT'});
});
it('loads the new cash ticket bracket presets instead of discarding them on instrument change',()=>{
 const options:OrderWindowOptions={symbol:'INFY',exchange:'NSE',initialSide:'BUY',lastPrice:100,initialSlPct:-5,initialTgtPct:10};
 const view=render(<OrderWindow options={options} onClose={close}/>);
 expect(screen.getByDisplayValue('-5')).toBeEnabled();
 view.rerender(<OrderWindow options={{...options,symbol:'TCS',lastPrice:200,initialSlPct:-2,initialTgtPct:20}} onClose={close}/>);
 expect(screen.getByDisplayValue('-2')).toBeEnabled();
 expect(screen.getByDisplayValue('20')).toBeEnabled();
 expect(mocks.place).not.toHaveBeenCalled();
});

it('ignores an old margin response after selecting a different ticket',()=>{
 vi.useFakeTimers();
 const options:OrderWindowOptions={symbol:'INFY',exchange:'NSE',initialSide:'BUY',lastPrice:100};
 const view=render(<OrderWindow options={options} onClose={close}/>);
 act(()=>vi.advanceTimersByTime(400));
 const stale=mocks.margin.mock.calls[0][1];
 view.rerender(<OrderWindow options={{...options,symbol:'TCS',lastPrice:200}} onClose={close}/>);
 act(()=>stale.onSuccess([{total:77777,charges:{total:0}}]));
 expect(screen.queryByText(/77,777/)).toBeNull();
 expect(mocks.place).not.toHaveBeenCalled();
});

it('does not apply the source signal stop to a different instrument selected in search',()=>{
 mocks.instruments=[{tradingsymbol:'TCS',exchange:'NSE',last_price:50,lot_size:1,name:'TCS'}];
 render(<OrderWindow options={{symbol:'TESTCE',exchange:'NFO',initialSide:'BUY',lotSize:50,lastPrice:100,fromSignal:true,planStop:80}} onClose={close}/>);
 fireEvent.click(screen.getByTitle('Search instrument'));
 fireEvent.click(screen.getByText('TCS',{exact:true}));
 expect(screen.getByRole('button',{name:/^Buy$/})).toBeEnabled();
 expect(screen.queryByText(/This plan's stop/)).toBeNull();
 expect(mocks.place).not.toHaveBeenCalled();
});

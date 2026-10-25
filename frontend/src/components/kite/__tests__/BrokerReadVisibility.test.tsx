import React from 'react';
import {beforeEach, expect, it, vi} from 'vitest';
import {fireEvent, render, screen} from '@testing-library/react';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {OrdersPane} from '../OrdersPane';
import {PositionsPane} from '../PositionsPane';
import {PortfolioPane} from '../PortfolioPane';

const h=vi.hoisted(()=>({positions:{} as any,orders:{} as any,holdings:{} as any,retry:vi.fn(),positionCalls:0}));
vi.mock('../../../hooks/useKite',()=>({
 useKitePositions:()=>{if(++h.positionCalls>25)throw new Error('Positions render loop');return h.positions;},
 useKiteOrders:()=>h.orders,useKiteHoldings:()=>h.holdings,
 useKiteLtp:()=>({data:undefined}),useKiteQuote:()=>({data:undefined}),
 useKiteWatchlist:()=>({add:vi.fn()}),useKiteAuctions:()=>({data:[]}),
 useConvertKitePosition:()=>({mutate:vi.fn()}),usePlaceKiteOrder:()=>({mutate:vi.fn()}),
 useInitiateHoldingsAuth:()=>({mutate:vi.fn()}),useCancelKiteOrder:()=>({mutate:vi.fn()}),
 useKiteMfSips:()=>({data:[]}),useCancelKiteMfSip:()=>({mutate:vi.fn()}),
}));
vi.mock('../EnginePositionsPane',()=>({EnginePositionsPane:()=>null}));
vi.mock('../KitePortfolioAnalyticsModal',()=>({KitePortfolioAnalyticsModal:()=>null}));
vi.mock('../KiteSettingsPopover',()=>({KiteSettingsPopover:()=>null}));
vi.mock('../GttPane',()=>({GttPane:()=>null}));
vi.mock('../AlertsPane',()=>({AlertsPane:()=>null}));
vi.mock('../InstrumentLabel',()=>({InstrumentLabel:({symbol}:any)=><span>{symbol}</span>}));
vi.mock('../ModifyOrderModal',()=>({ModifyOrderModal:()=>null}));
vi.mock('../OrderHistoryRow',()=>({OrderHistoryRow:()=>null}));
beforeEach(()=>{
 localStorage.clear();sessionStorage.clear();vi.clearAllMocks();h.positionCalls=0;
 h.positions={data:{net:[],day:[]},error:null,refetch:h.retry};
 h.orders={data:[],error:null,refetch:h.retry};h.holdings={data:[],error:null,refetch:h.retry};
});
function show(node:React.ReactNode){return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);}
const views=[
 ['orders',()=> <OrdersPane/>,()=>h.orders,/haven't placed any orders/],
 ['positions',()=> <PositionsPane/>,()=>h.positions,/0 open/],
 ['positions',()=> <PortfolioPane view="positions"/>,()=>h.positions,/No open positions/],
 ['holdings',()=> <PortfolioPane view="holdings"/>,()=>h.holdings,/No equity holdings/],
] as const;
for(const [index,[label,node,query,empty]] of views.entries()){
 it(`shows unavailable and retries instead of an empty claim in view ${index}`,()=>{
  Object.assign(query(),{data:undefined,error:new Error('502 Bad Gateway')});
  show(node());expect(screen.getByRole('alert')).toHaveTextContent(/unavailable/i);
  expect(screen.getByRole('alert')).toHaveTextContent(/unknown/i);
  expect(screen.queryByText(empty)).toBeNull();
  expect(screen.queryByText('Total P&L')).toBeNull();
  if(index>0){
   expect(screen.getByRole('button',{name:/Analyze/})).toBeDisabled();
   expect(screen.getByRole('button',{name:/Download/})).toBeDisabled();
  }
  fireEvent.click(screen.getByRole('button',{name:`Retry ${label}`}));expect(h.retry).toHaveBeenCalledOnce();
 });
 it(`distinguishes loading from an empty response in view ${index}`,()=>{
  Object.assign(query(),{data:undefined,isLoading:true});show(node());
  expect(screen.getByRole('status')).toHaveTextContent(/Loading broker/i);
  expect(screen.queryByText(empty)).toBeNull();expect(screen.queryByRole('alert')).toBeNull();
  if(index>0)expect(screen.getByRole('button',{name:/Download/})).toBeDisabled();
 });
}
it('keeps last known orders with an explicit stale warning until recovery',()=>{
 h.orders={data:[{order_id:'1',tradingsymbol:'TEST',status:'COMPLETE',quantity:1}],error:new Error('502'),refetch:h.retry};
 const view=show(<OrdersPane/>);expect(screen.getByText('TEST')).toBeInTheDocument();
 expect(screen.getByRole('alert')).toHaveTextContent(/last known/i);
 h.orders={data:[],error:null,refetch:h.retry};view.rerender(<QueryClientProvider client={new QueryClient()}><OrdersPane/></QueryClientProvider>);
 expect(screen.queryByRole('alert')).toBeNull();expect(screen.getByText(/haven't placed any orders/)).toBeInTheDocument();
});
it('does not turn a failed refresh of a previously empty portfolio into confirmed flat exposure',()=>{
 h.positions.error=new Error('502');show(<PortfolioPane view="positions"/>);
 expect(screen.getByRole('alert')).toHaveTextContent(/last known/i);
 expect(screen.queryByText('No open positions.')).toBeNull();
});

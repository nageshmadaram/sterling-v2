import React from 'react';
import {render,screen,cleanup,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {PcrPane} from './PcrPane';
import type {PcrDeskPayload} from '../../lib/pcr/types';
import {fetchPcrDesk,normalizePcrPayload} from '../../lib/pcr/fetchPcr';
const quotes=vi.hoisted(()=>({data:{} as Record<string,unknown>}));
vi.mock('../../lib/pcr/fetchPcr',async()=>({...await vi.importActual('../../lib/pcr/fetchPcr'),fetchPcrDesk:vi.fn()}));
vi.mock('../../hooks/useKite',()=>({useKiteQuote:()=>quotes}));
afterEach(()=>{cleanup();vi.useRealTimers();vi.clearAllMocks();localStorage.clear();quotes.data={};});
const data=(over:Partial<PcrDeskPayload>):PcrDeskPayload=>({asOf:'2026-10-23T15:30:00+05:30',source:'snapshot',marketOpen:false,series:{} as PcrDeskPayload['series'],...over});
it.each([
 ['snapshot',data({}),/Historical PCR.*2026-10-23.*not current/],
 ['closed feed',data({source:'live'}),/Market closed.*2026-10-23/],
 ['old live feed',data({source:'live',marketOpen:true}),/Historical PCR.*2026-10-23.*not current/],
] as const)('shows %s provenance without opening an information dialog',async(_name,payload,expected)=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2026-10-24T02:00:00+05:30'));
 vi.mocked(fetchPcrDesk).mockResolvedValue(payload);
 render(<PcrPane/>);
 await waitFor(()=>expect(screen.getByRole('status',{name:'PCR data status'})).toHaveTextContent(expected));
 expect(screen.queryByRole('dialog',{name:'PCR data information'})).not.toBeInTheDocument();
});

it('keeps historical PCR spot and change tied to their recorded session',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2026-10-24T02:00:00+05:30'));
 quotes.data={'NSE:NIFTY 50':{last_price:28000,ohlc:{close:27000}}};
 vi.mocked(fetchPcrDesk).mockResolvedValue(data({series:normalizePcrPayload({series:{NIFTY:{id:'NIFTY',expiry:'2026-10-27',livePcr:0.74,spot:{ltp:22600,changePer:-0.39,maxPain:null,vix:null,timestamp:'2026-10-23T15:30:00+05:30'},marks:[{hhmm:'15:30',pcr:0.74,volumePcr:0.74,changeOiPcr:0,indexClose:22600}],latest:null}}})!.series}));
 render(<PcrPane/>);
 await waitFor(()=>expect(screen.getByRole('status',{name:'PCR data status'})).toHaveTextContent('Historical PCR'));
 expect(screen.queryByText(/28,000/)).not.toBeInTheDocument();
 expect(screen.getByText(/22,600/)).toBeInTheDocument();
});

it('still uses live quotes for the current active PCR session',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2026-10-23T15:00:00+05:30'));
 quotes.data={'NSE:NIFTY 50':{last_price:28000,ohlc:{close:27000}}};
 vi.mocked(fetchPcrDesk).mockResolvedValue(data({asOf:'2026-10-23T14:59:00+05:30',source:'live',marketOpen:true,series:normalizePcrPayload({series:{NIFTY:{id:'NIFTY',expiry:'2026-10-27',livePcr:0.74,spot:{ltp:22600,changePer:-0.39,maxPain:null,vix:null,timestamp:'2026-10-23T14:59:00+05:30'},marks:[],latest:null}}})!.series}));
 render(<PcrPane/>);
 await waitFor(()=>expect(screen.getByRole('status',{name:'PCR data status'})).toHaveTextContent('PCR observation'));
 expect(screen.getByText(/28,000/)).toBeInTheDocument();
 expect(screen.queryByText(/22,600/)).not.toBeInTheDocument();
});

it('keeps feed failure visible when no observation can be loaded',async()=>{
 vi.mocked(fetchPcrDesk).mockRejectedValue(new Error('feed unavailable'));
 render(<PcrPane/>);
 await waitFor(()=>expect(screen.getByRole('status',{name:'PCR data status'})).toHaveTextContent('PCR unavailable'));
});

it('shows the forming boundary and actual print time in the tile, heat and tape',async()=>{
 vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date('2026-10-26T12:05:00+05:30'));
 const mark=(hhmm:string,pcr:number)=>({hhmm,pcr,volumePcr:1,changeOiPcr:0,indexClose:22600});
 vi.mocked(fetchPcrDesk).mockResolvedValue(data({asOf:'2026-10-26T12:05:00+05:30',source:'live',marketOpen:true,series:normalizePcrPayload({series:{NIFTY:{id:'NIFTY',expiry:'2026-10-27',livePcr:0.9,spot:{ltp:22600,changePer:0,maxPain:null,vix:null,timestamp:'2026-10-26T12:05:00+05:30'},marks:[mark('12:00',0.8)],latest:mark('12:05',0.9)}}})!.series}));
 const {container}=render(<PcrPane/>);
 const label='Forming until 12:15 · observed 12:05';
 await waitFor(()=>expect(container.querySelector('.kp-tile')).toHaveTextContent(label));
 expect(container.querySelector('.kp-heat-row[data-live="true"]')).toHaveTextContent(label);
 expect(container.querySelector('.kp-tape-row[data-live="true"]')).toHaveTextContent(label);
});

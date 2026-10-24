import React from 'react';
import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import {supertrendLegToBoard} from '../supertrendAdapter';
import {useBoardRowActions} from '../useBoardRowActions';
import type {EngineSignalRow,OptionLeg} from '../../../../types/kiteEngine';

const row={underlying:'NIFTY',token:1,exchange:'NFO',direction:'long',option_type:'CE',regime:'BULL',alignment:{fast:1,mid:1,slow:1},legs:[],spot:24000,score:82,stop_loss:23900,timestamp_ms:Date.now(),is_active:false,exit_reason:'trail breach (≤ 100.00)'} as EngineSignalRow;
function leg(symbol:string,trail:number|null):OptionLeg{return {option_symbol:symbol,option_type:'CE',moneyness:'ATM',premium_spot:200,entry_sl:80,premium_sl:trail,lot_size:75} as OptionLeg;}
function Actions({signal}:{signal:ReturnType<typeof supertrendLegToBoard>}){return <>{useBoardRowActions().renderTrade(signal)}</>;}
it('uses each contract stop in disabled entry, exit badge, flags and exit detail',()=>{
 const signal=supertrendLegToBoard(row,leg('SECOND',185),0);
 render(<Actions signal={signal}/>);
 expect(screen.getByRole('button',{name:'B'})).toHaveAttribute('title','Stop hit 185.00');
 expect(signal.marks?.find(x=>x.label==='TSL exit')?.hint).toContain('185.00');
 expect(signal.flags?.find(x=>x.label==='TSL exit')?.hint).toContain('185.00');
 expect(signal.sections.find(x=>x.title==='Exit rule')?.stats?.find(x=>x.label==='Why it ended')?.value).toContain('185.00');
 expect(signal.reason).toBe(row.exit_reason); // Preserve raw engine diagnostic separately.
});
it('never borrows another contract stop when this leg has no known trail',()=>{
 const signal=supertrendLegToBoard(row,leg('UNKNOWN',null),0);
 expect(signal.exitProgress).toBe('Stop hit');
 expect(signal.marks?.find(x=>x.label==='TSL exit')?.hint).not.toContain('100.00');
 expect(signal.flags?.find(x=>x.label==='TSL exit')?.hint).not.toContain('100.00');
});

it('does not end an active derivative because a different contract ended',()=>{
 const signal=supertrendLegToBoard({...row,source:'derivatives',is_active:true}, {...leg('RUNNING',185),is_active:true,exit_reason:null,entry_timestamp_ms:row.timestamp_ms},0);
 expect(signal.status).toBe('running');
 expect(signal.executionEligible).toBe(true);
 expect(signal.flags?.some(x=>x.label==='TSL exit')).toBe(false);
});
it('uses a derivative contract own exit rule and leaves legacy unknowns unknown',()=>{
 const own=supertrendLegToBoard({...row,source:'derivatives'}, {...leg('RED',185),is_active:false,exit_reason:'red count exit 1/1'},0);
 expect(own.exitProgress).toBe('Trend turned red');
 expect(own.flags?.find(x=>x.label==='counter exit')?.hint).toContain('red count exit 1/1');
 const legacy=supertrendLegToBoard({...row,source:'derivatives'}, {...leg('OLD',185),is_active:false},0,{quotes:{'NFO:OLD':{last_price:100}}});
 expect(legacy.exitProgress).toBe('Closed');
 expect(legacy.flags?.some(x=>x.label==='TSL exit')).toBe(false);
});
it('does not borrow a grouped derivative counter or alignment when a leg is unknown',()=>{
 const signal=supertrendLegToBoard({...row,source:'derivatives',is_active:true,exit_state:'3/3 red'}, {...leg('UNKNOWN',185),is_active:true,exit_state:null,alignment:null},0);
 expect(signal.status).toBe('running');
 expect(signal.executionEligible).toBe(false); // Unknown legacy contract date cannot authorize entry.
 const detail=signal.sections.find(x=>x.title==='Exit rule');
 expect(detail?.stats?.find(x=>x.label==='Red counter')?.value).toBe('—');
 expect(signal.sections.find(x=>x.title==='Trend & volatility')?.stats?.find(x=>x.label==='Alignment')?.value).toBe('—');
});
it('shows the running contract counter and freshness instead of the group summary',()=>{
 const signal=supertrendLegToBoard({...row,source:'derivatives',is_active:true,is_fresh:true,exit_state:'3/3 red'}, {...leg('OLDER_RUNNING',185),is_active:true,exit_state:'0/3 red',entry_timestamp_ms:row.timestamp_ms-60000},0);
 expect(signal.status).toBe('running');
 expect(signal.exitProgress).toBe('0/3 red');
});
it('shows no invented group date and explains why a legacy contract cannot enter',()=>{
 const signal=supertrendLegToBoard({...row,source:'derivatives',is_active:true}, {...leg('UNDATED',185),is_active:true},0);
 expect(signal.atMs).toBeNull();
 expect(signal.executionEligible).toBe(false);
 expect(signal.reason).toMatch(/signal time is unavailable.*fresh scan/i);
});
it('keeps a mixed derivative group summary separate from its first contract',async()=>{
 const {supertrendToBoard}=await import('../supertrendAdapter');
 const signals=supertrendToBoard([{...row,source:'derivatives',spot:0,is_active:true,exit_state:'3/3 red',legs:[
  {...leg('STOPPED',100),is_active:false,entry_timestamp_ms:row.timestamp_ms,exit_reason:'trail breach (≤ 100.00)'},
  {...leg('RUNNING',185),is_active:true,entry_timestamp_ms:row.timestamp_ms,exit_state:'0/3 red',exit_reason:null},
 ]}]);
 const parent=signals[0];
 expect(parent.status).toBe('running');
 expect(parent.marks?.some(x=>x.label==='TSL exit')).toBe(false);
 expect(parent.exitProgress).toBe('Per contract');
 expect(parent.reason).toBeNull();
 expect(parent.sections.find(x=>x.title==='Trend & volatility')?.stats?.find(x=>x.label==='Spot')?.value).toBe('—');
 expect(parent.sections.find(x=>x.title==='Trend & volatility')?.stats?.find(x=>x.label==='Alignment')?.value).toBe('—');
});

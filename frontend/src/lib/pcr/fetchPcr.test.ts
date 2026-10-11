import {describe,expect,it} from 'vitest';
import {normalizePcrPayload} from './fetchPcr';
describe('PCR feed and mobile cache normalization',()=>{
  it('fills missing spot and marks without inventing quotes',()=>{
    const result=normalizePcrPayload({source:'live',series:{NIFTY:{expiry:'2026-10-13',marks:null,latest:null}}});
    expect(result?.series.NIFTY.marks).toEqual([]);
    expect(result?.series.NIFTY.spot.ltp).toBeNull();
    expect(result?.source).toBe('live');
  });
  it('rejects absent series and discards malformed marks',()=>{
    expect(normalizePcrPayload({series:null})).toBeNull();
    expect(normalizePcrPayload({series:{NIFTY:{marks:[null,{}, {hhmm:'09:15',pcr:1.2}]}}})?.series.NIFTY.marks).toHaveLength(1);
  });
});

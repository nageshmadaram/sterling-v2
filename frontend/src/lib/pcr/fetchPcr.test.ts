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

import {vi} from 'vitest';
import {fetchPcrDesk,rememberSession} from './fetchPcr';
import {api} from '../../utils/api';
it('restores the last recorded session after a closed-session API restart',async()=>{
  localStorage.removeItem('sterling.pcr.books.v1');
  rememberSession(normalizePcrPayload({asOf:'2026-10-12T15:30:00+05:30',source:'live',series:{NIFTY:{marks:[{hhmm:'15:30',pcr:0.75}],latest:{hhmm:'15:30',pcr:0.75}}}})!);
  const request=vi.spyOn(api,'get').mockResolvedValue({asOf:'2026-10-12T16:00:00+05:30',marketOpen:false,sessionPhase:'closed',source:'snapshot',series:{}});
  try {
    const result=await fetchPcrDesk();
    expect(result.series.NIFTY.latest?.pcr).toBe(0.75);
    expect(result.asOf).toBe('2026-10-12T15:30:00+05:30');
    expect(result.source).toBe('snapshot');expect(result.marketOpen).toBe(false);
  } finally {request.mockRestore();localStorage.removeItem('sterling.pcr.books.v1');}
});

it('falls back to snapshotPayload when fresh mobile device has no cached sessions', async () => {
  localStorage.removeItem('sterling.pcr.books.v1');
  const request = vi.spyOn(api, 'get').mockResolvedValue({
    asOf: '2026-10-12T16:00:00+05:30',
    marketOpen: false,
    sessionPhase: 'closed',
    source: 'snapshot',
    series: {},
  });
  try {
    const result = await fetchPcrDesk();
    expect(result.series.NIFTY.latest?.pcr).toBeDefined();
    expect(result.series.BANKNIFTY.latest?.pcr).toBeDefined();
    expect(result.marketOpen).toBe(false);
    expect(result.source).toBe('snapshot');
  } finally {
    request.mockRestore();
    localStorage.removeItem('sterling.pcr.books.v1');
  }
});

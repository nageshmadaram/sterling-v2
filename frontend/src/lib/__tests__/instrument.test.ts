import { describe, expect, it } from 'vitest';
import { formatInstrument, parseInstrument } from '../instrument';

describe('display contract identity', () => {
  it.each([
    ['BANKNIFTY26NOV55200CE', 'BANKNIFTY NOV 55200 CE'],
    ['NFO:BANKNIFTY26NOV55200CE', 'BANKNIFTY NOV 55200 CE'],
    ['NIFTY26O0925000PE', 'NIFTY 9 OCT 25000 PE'],
    ['BAJAJ-AUTO26JUL10500CE', 'BAJAJ-AUTO JUL 10500 CE'],
    ['M&M26NOV3000PE', 'M&M NOV 3000 PE'],
    ['SENSEX5026SEPFUT', 'SENSEX50 SEP FUT'],
    ['NSE:RELIANCE', 'RELIANCE'],
    ['', '—'],
  ])('formats %s without changing its broker identity', (raw, display) => {
    expect(formatInstrument(raw)).toBe(display);
  });
  it('keeps weekly days distinct', () => {
    expect(formatInstrument('NIFTY26O0925000CE')).not.toBe(formatInstrument('NIFTY26O1625000CE'));
    expect(parseInstrument('SENSEX5026SEPFUT')?.underlying).toBe('SENSEX50');
  });
});

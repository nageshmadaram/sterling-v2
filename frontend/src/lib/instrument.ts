export interface ParsedParts {
  underlying: string;
  strike?: string;
  type?: string; // CE / PE / FUT
  day?: number;
  month?: string; // JUN
  year?: string; // 24
  isWeekly?: boolean;
}

const MONTHS = 'JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC';
const UNDERLYING = '(?:[A-Z0-9&-]+?)';

export function parseInstrument(ts: string): ParsedParts | null {
  const normalized = (ts || '').trim().toUpperCase();

  // Monthly NFO options: APLAPOLLO26JUN1820CE, BAJAJ-AUTO26JUL10500CE
  const nfoRe = new RegExp(`^(${UNDERLYING})(\\d{2})(${MONTHS})(\\d+)(CE|PE)(BFO|NFO)?$`);
  const nfoM = normalized.match(nfoRe);
  if (nfoM) {
    return {
      underlying: nfoM[1],
      year: nfoM[2],
      month: nfoM[3].toUpperCase(),
      strike: nfoM[4],
      type: nfoM[5],
      isWeekly: false,
    };
  }

  // Weekly NFO options: NIFTY2461324500CE. Underlyings may contain - or &.
  const weeklyMatch = normalized.match(new RegExp(`^(${UNDERLYING})(\\d{2})(1|2|3|4|5|6|7|8|9|O|N|D)(\\d{2})(\\d+)(CE|PE)(BFO|NFO)?$`));
  if (weeklyMatch) {
    const mMap: Record<string, string> = {'1':'JAN','2':'FEB','3':'MAR','4':'APR','5':'MAY','6':'JUN','7':'JUL','8':'AUG','9':'SEP','O':'OCT','N':'NOV','D':'DEC'};
    return {
      underlying: weeklyMatch[1],
      year: weeklyMatch[2],
      month: mMap[weeklyMatch[3]],
      day: parseInt(weeklyMatch[4], 10),
      strike: weeklyMatch[5],
      type: weeklyMatch[6],
      isWeekly: true,
    };
  }

  // NFO Futures: NIFTY24JUNFUT, BAJAJ-AUTO26JULFUT
  // NFO Futures: NIFTY24JUNFUT, BAJAJ-AUTO26JULFUT, SENSEX26SEPFUT, SENSEX5026SEPFUT
  const futRe = new RegExp(`^(${UNDERLYING})(\\d{2})(${MONTHS})FUT(BFO|NFO)?$`);
  const futM = normalized.match(futRe);
  if (futM) {
    return {
      underlying: futM[1],
      year: futM[2],
      month: futM[3].toUpperCase(),
      type: 'FUT',
      isWeekly: false,
    };
  }

  // BSE options: SENSEX2461875500CE
  // BSE options: SENSEX2461875500CE, SENSEX502461875500CE
  const bseRe = new RegExp(`^(${UNDERLYING})(\\d{2})([1-9A-COND])(\\d{2})(\\d+)(CE|PE)$`);
  const bseM = normalized.match(bseRe);
  if (bseM) {
    let mon = parseInt(bseM[3], 10);
    if (bseM[3] === 'O' || bseM[3] === 'A') mon = 10;
    if (bseM[3] === 'N' || bseM[3] === 'B') mon = 11;
    if (bseM[3] === 'D' || bseM[3] === 'C') mon = 12;
    if (mon >= 1 && mon <= 12) {
      const d = new Date(2000 + Number(bseM[2]), mon - 1, 1);
      const monthStr = d.toLocaleString('en-US', { month: 'short' }).toUpperCase();
      return {
        underlying: bseM[1],
        year: bseM[2],
        month: monthStr,
        day: parseInt(bseM[4], 10),
        strike: bseM[5],
        type: bseM[6],
        isWeekly: true,
      };
    }
  }

  return null;
}

/** Display identity only: never use this text as a broker symbol or subscription key. */
export function formatInstrument(symbol: string | null | undefined): string {
  const raw = (symbol ?? '').trim();
  if (!raw) return '—';
  const tradingsymbol = raw.includes(':') ? raw.slice(raw.indexOf(':') + 1) : raw;
  const parts = parseInstrument(tradingsymbol);
  if (!parts) return tradingsymbol;
  return [parts.underlying, parts.isWeekly ? parts.day : null, parts.month, parts.strike, parts.type]
    .filter(value => value != null && value !== '').join(' ');
}


/** Human-readable contracts inside an activity/error sentence; underlying data stays raw. */
export function formatInstrumentText(text: string): string {
  return text.replace(/\b[A-Z0-9&-]+(?:CE|PE|FUT)\b/g, raw => parseInstrument(raw) ? formatInstrument(raw) : raw);
}

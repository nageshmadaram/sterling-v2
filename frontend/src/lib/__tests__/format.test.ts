import { describe, it, expect } from 'vitest';
import {
  EM_DASH, LABELS, fmtAgeSeconds, fmtDateTimeIST, fmtMoney, fmtPct, fmtPnl, fmtPx, fmtQty,
  fmtTimeIST, pnlColor, pnlTone,
} from '../format';

describe('money and P&L', () => {
  it('prints rupees with two decimals and Indian grouping', () => {
    expect(fmtMoney(1234.5)).toBe('₹1,234.50');
    expect(fmtMoney(12345678)).toBe('₹1,23,45,678.00');
    expect(fmtMoney(-43.75)).toBe('-₹43.75');
  });

  it('never prints a sign in front of zero', () => {
    expect(fmtMoney(-0.001)).toBe('₹0.00');
    expect(fmtPnl(-0.004)).toBe('₹0.00');
    expect(fmtPx(-0.0001)).toBe('0.00');
  });

  it('signs P&L explicitly', () => {
    expect(fmtPnl(5957.5)).toBe('+₹5,957.50');
    expect(fmtPnl(-43.75)).toBe('-₹43.75');
    expect(fmtPnl(0)).toBe('₹0.00');
  });

  it('shows a dash for anything that is not a number', () => {
    for (const bad of [null, undefined, NaN, Infinity, '', 'abc']) {
      expect(fmtMoney(bad)).toBe(EM_DASH);
      expect(fmtPnl(bad)).toBe(EM_DASH);
      expect(fmtPx(bad)).toBe(EM_DASH);
      expect(fmtQty(bad)).toBe(EM_DASH);
      expect(fmtPct(bad)).toBe(EM_DASH);
    }
  });

  it('accepts numeric strings from the API', () => {
    expect(fmtMoney('100')).toBe('₹100.00');
  });

  it('colours by sign using theme tokens', () => {
    expect(pnlTone(10)).toBe('gain');
    expect(pnlTone(-10)).toBe('loss');
    expect(pnlTone(0)).toBe('flat');
    expect(pnlTone(0.001)).toBe('flat');
    expect(pnlColor(10)).toBe('var(--k-green)');
    expect(pnlColor(-10)).toBe('var(--k-red)');
    expect(pnlColor(null)).toBe('var(--k-dim)');
  });
});

describe('quantity and percent', () => {
  it('groups quantities and rounds to whole units', () => {
    expect(fmtQty(1800)).toBe('1,800');
    expect(fmtQty(75.4)).toBe('75');
  });

  it('formats percentages', () => {
    expect(fmtPct(12.5)).toBe('12.50%');
    expect(fmtPct(12.5, 1, true)).toBe('+12.5%');
    expect(fmtPct(-3)).toBe('-3.00%');
    expect(fmtPct(0, 2, true)).toBe('0.00%');
  });
});

describe('IST times', () => {
  // 2026-10-05T03:45:03Z is 09:15:03 IST
  const utc = Date.UTC(2026, 9, 5, 3, 45, 3);

  it('prints HH:MM:SS in IST regardless of the browser zone', () => {
    expect(fmtTimeIST(utc)).toBe('09:15:03');
    expect(fmtTimeIST(utc, false)).toBe('09:15');
  });

  it('accepts epoch seconds, ISO strings and Dates', () => {
    expect(fmtTimeIST(Math.floor(utc / 1000))).toBe('09:15:03');
    expect(fmtTimeIST('2026-10-05T03:45:03Z')).toBe('09:15:03');
    expect(fmtTimeIST(new Date(utc))).toBe('09:15:03');
  });

  it('uses 24-hour time, midnight is 00 not 24', () => {
    expect(fmtTimeIST(Date.UTC(2026, 9, 4, 18, 30, 0))).toBe('00:00:00');
    expect(fmtTimeIST(Date.UTC(2026, 9, 5, 10, 0, 0))).toBe('15:30:00');
  });

  it('adds the IST date when asked', () => {
    expect(fmtDateTimeIST(utc)).toBe('05 Oct 09:15:03');
  });

  it('dashes unusable timestamps', () => {
    for (const bad of [null, undefined, 0, -1, 'nope', NaN]) {
      expect(fmtTimeIST(bad)).toBe(EM_DASH);
    }
  });
});

describe('age and labels', () => {
  it('compacts elapsed seconds', () => {
    expect(fmtAgeSeconds(12)).toBe('12s');
    expect(fmtAgeSeconds(240)).toBe('4m');
    expect(fmtAgeSeconds(7200)).toBe('2h');
    expect(fmtAgeSeconds(172800)).toBe('2d');
    expect(fmtAgeSeconds(-1)).toBe(EM_DASH);
  });

  it('exposes the shared short labels', () => {
    expect(LABELS).toMatchObject({
      entry: 'Entry', ltp: 'LTP', sl: 'SL', tsl: 'TSL', target: 'Target', exit: 'Exit',
      pnl: 'P&L', qty: 'Qty', risk: 'Risk', capital: 'Capital', status: 'Status',
    });
  });
});

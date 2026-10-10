import { describe, expect, it } from 'vitest';
import { positionMetrics } from '../enginePositionMetrics';

const base = {
  fill_price: 100, entry_premium: 99, stop_premium: 90, initial_stop_premium: 90,
  qty: 150, direction: 'long' as const, vehicle: 'otm_options' as const,
};

describe('positionMetrics', () => {
  it('prices P&L in rupees on UNITS, against the fill, not the intended entry', () => {
    const m = positionMetrics(base, 110);
    expect(m.entry).toBe(100);
    expect(m.pnlInr).toBe(1500);            // (110 - 100) x 150 units; lot size never re-applied
    expect(m.pnlPct).toBeCloseTo(10);
  });

  it('has no P&L without a live price instead of showing a flat zero', () => {
    const m = positionMetrics(base, null);
    expect(m.ltp).toBeNull();
    expect(m.pnlInr).toBeNull();
    expect(m.pnlPct).toBeNull();
  });

  it('negates P&L for a short future', () => {
    const m = positionMetrics({ ...base, direction: 'short', vehicle: 'futures', stop_premium: 110, initial_stop_premium: 110 }, 95);
    expect(m.pnlInr).toBe(750);             // price fell 5 on a short: profit
    expect(m.capitalInr).toBeNull();        // margined, not paid for
  });

  it('names the stop SL until it has trailed, then TSL', () => {
    expect(positionMetrics(base, 100).stopKind).toBe('SL');
    expect(positionMetrics({ ...base, stop_premium: 112 }, 120).stopKind).toBe('TSL');
    expect(positionMetrics({ ...base, initial_stop_premium: undefined }, 120).stopKind).toBe('SL');
  });

  it('sizes risk from the ENTRY stop and capital from the premium paid', () => {
    const m = positionMetrics({ ...base, stop_premium: 112 }, 120);
    expect(m.riskInr).toBe(1500);           // (100 - 90) x 150, not the trailed stop
    expect(m.capitalInr).toBe(15000);       // 100 x 150
  });

  it('reports slippage as adverse-positive, so paying more reads as a cost', () => {
    expect(positionMetrics({ ...base, fill_price: 101, entry_premium: 100 }, 101).slippagePct).toBeCloseTo(1);
    expect(positionMetrics({ ...base, fill_price: 99, entry_premium: 100 }, 99).slippagePct).toBeCloseTo(-1);
    const short = positionMetrics({ ...base, direction: 'short', fill_price: 99, entry_premium: 100 }, 99);
    expect(short.slippagePct).toBeCloseTo(1); // sold lower than intended: adverse
  });

  it('treats a zero stop as no stop', () => {
    const m = positionMetrics({ ...base, stop_premium: 0 }, 100);
    expect(m.stop).toBeNull();
    expect(m.stopKind).toBeNull();
  });
});

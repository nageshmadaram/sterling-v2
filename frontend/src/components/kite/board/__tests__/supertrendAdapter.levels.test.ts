import { describe, expect, it } from 'vitest';
import { supertrendLegToBoard, supertrendToBoard } from '../supertrendAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';

const leg: OptionLeg = {
  moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY26OCT24000CE', strike: 24000,
  expiry: '2026-10-27', lot_size: 75, premium_spot: 100, entry_sl: 90, premium_sl: 105,
  premium_target: null, is_active: true, token: 9001,
};
const row = {
  underlying: 'NIFTY 50', token: 256265, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE', legs: [leg],
  spot: 24100, stop_loss: 24000, score: 85, timestamp_ms: 1_760_000_000_000,
  is_active: true, is_fresh: false, source: 'spot', exit_state: '0/1 red',
} as unknown as EngineSignalRow;

describe('supertrend board levels', () => {
  it('allows an active setup from today after its signal candle, while blocking prior days and replay', () => {
    const nowMs = Date.UTC(2026, 9, 12, 8);
    const today = { ...row, timestamp_ms: Date.UTC(2026, 9, 12, 4), is_fresh: false };
    expect(supertrendLegToBoard(today, leg, 0, { nowMs }).executionEligible).toBe(true);
    expect(supertrendLegToBoard({ ...today, timestamp_ms: today.timestamp_ms - 86400000 }, leg, 0, { nowMs }).executionEligible).toBe(false);
    expect(supertrendLegToBoard({ ...today, is_replay: true }, leg, 0, { nowMs }).executionEligible).toBe(false);
    expect(supertrendLegToBoard(today, { ...leg, is_active: false }, 0, { nowMs }).executionEligible).toBe(false);
  });
  it('never applies present quotes or spot to retained replay rows', () => {
    const signal = supertrendLegToBoard({ ...row, is_replay: true }, leg, 0, {
      quotes: { 'NFO:NIFTY26OCT24000CE': { last_price: 999, ohlc: { close: 100 } } },
      spotOf: () => 99999,
    });
    expect(signal.historical).toBe(true);
    expect(signal.levels.ltp).toBeNull();
    expect(signal.dayMove).toBeNull();
  });
  it('leaves LTP blank without a quote rather than echoing the entry', () => {
    const s = supertrendLegToBoard(row, leg, 0, {});
    expect(s.levels.ltp).toBeNull();
    expect(s.levels.entry).toBe(100);
  });

  it('takes LTP from the live quote only', () => {
    const s = supertrendLegToBoard(row, leg, 0, {
      quotes: { 'NFO:NIFTY26OCT24000CE': { last_price: 88 } },
    });
    expect(s.levels.ltp).toBe(88);
  });

  it('never invents a target or an exit price for a SuperTrend leg', () => {
    const s = supertrendLegToBoard(row, leg, 0, {});
    expect(s.levels.target).toBeNull();
    expect(s.levels.exit).toBeNull();
  });

  it('reports risk and capital for ONE lot from the entry stop and premium', () => {
    const s = supertrendLegToBoard(row, leg, 0, {});
    expect(s.sizing.atRiskInr).toBe((100 - 90) * 75);
    expect(s.sizing.deployedInr).toBe(100 * 75);
  });
});

describe('signal time', () => {
  // 2026-10-07 10:15 IST is the OPEN of the candle that closes at 11:15.
  const open1015 = Date.UTC(2026, 9, 7, 4, 45);
  const open1515 = Date.UTC(2026, 9, 7, 9, 45);

  it('is the candle CLOSE, matching the order path and the alerts', async () => {
    const { barCloseMs } = await import('../boardTypes');
    expect(barCloseMs(open1015)).toBe(open1015 + 3_600_000);
    // The last session bar is 15 minutes long: 15:15 -> 15:30.
    expect(barCloseMs(open1515)).toBe(open1515 + 15 * 60_000);
    expect(barCloseMs(null)).toBeNull();
  });

  it('is what the board row carries', () => {
    const r = { ...row, timestamp_ms: open1015 } as EngineSignalRow;
    const l = { ...leg, entry_timestamp_ms: open1015, signal_timestamp_ms: open1015 } as OptionLeg;
    expect(supertrendLegToBoard(r, l, 0, {}).atMs).toBe(open1015 + 3_600_000);
  });
});


describe('signal price reference clarity', () => {
  it('shows this leg strike distance and strike-step classification at signal time', () => {
    const other = { ...leg, moneyness: 'OTM1', strike: 24200 };
    const signal = supertrendLegToBoard(row, other, 0, {});
    const facts = signal.sections.flatMap(section => section.stats);
    expect(facts.find(f => f.label === 'Strike − signal spot')?.value).toBe('+100.00 pts');
    expect(facts.find(f => f.label === 'Moneyness (strike steps)')?.value).toBe('OTM1');
  });
  it('blocks a new entry when the known live premium is already below its hard stop', () => {
    const nowMs = Date.UTC(2026, 9, 12, 8);
    const active = { ...row, timestamp_ms: Date.UTC(2026, 9, 12, 4) };
    const signal = supertrendLegToBoard(active, leg, 0, { nowMs,
      quotes: { 'NFO:NIFTY26OCT24000CE': { last_price: 75 } } });
    expect(signal.executionEligible).toBe(false);
    expect(signal.flags?.some(flag => flag.label === 'PRICE ≤ STOP')).toBe(true);
  });
});


describe('mode-specific completed signal timestamps', () => {
  it.each([1, 5, 15, 60, 120])('uses the row candle size of %s minutes for parent and leg', minutes => {
    const open = Date.UTC(2026, 9, 26, 4, 45);
    const r = { ...row, timestamp_ms: open, signal_timeframe_minutes: minutes } as EngineSignalRow;
    expect(supertrendToBoard([r], {})[0].atMs).toBe(open + minutes * 60_000);
    expect(supertrendLegToBoard(r, { ...leg, entry_timestamp_ms: open }, 0, {}).atMs).toBe(open + minutes * 60_000);
  });
  it('keeps Navigator event timestamps and retained replay timestamps unchanged', () => {
    const open = Date.UTC(2026, 9, 26, 4, 45);
    for (const extra of [{ source: 'navigator' }, { is_replay: true }]) {
      const r = { ...row, ...extra, timestamp_ms: open, signal_timeframe_minutes: 1 } as EngineSignalRow;
      expect(supertrendToBoard([r], {})[0].atMs).toBe(open);
    }
  });
});

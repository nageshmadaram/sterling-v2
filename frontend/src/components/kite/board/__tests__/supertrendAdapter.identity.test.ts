import { describe, expect, it } from 'vitest';
import { supertrendToBoard } from '../supertrendAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';

const leg = { option_symbol: 'BHARTIARTL26NOV1920CE', option_type: 'CE', moneyness: 'ATM',
  token: 123, lot_size: 475, strike: 1920, expiry: '2026-11-24', premium_spot: 40.7,
  entry_sl: 34.8, premium_sl: 34.8, is_active: true } as OptionLeg;
const row = { underlying: 'BHARTIARTL', token: 2714625, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE', legs: [leg],
  spot: 1899, stop_loss: 1850, score: 80, timestamp_ms: Date.UTC(2026, 9, 26, 4, 45),
  source: 'derivatives', horizon_mode: 'swing', signal_timeframe_minutes: 60,
  is_active: true, is_fresh: false } as EngineSignalRow;

function childId(input: EngineSignalRow) { return supertrendToBoard([input])[0].children![0].id; }

describe('contract disclosure identity', () => {
  it.each([
    ['mode', { horizon_mode: 'ultra_scalping', signal_timeframe_minutes: 1 }],
    ['signal occurrence', { timestamp_ms: row.timestamp_ms + 60_000 }],
    ['candle size', { signal_timeframe_minutes: 30 }],
    ['source', { source: 'spot' }],
  ] as const)('keeps the same contract separate across %s', (_, changes) => {
    expect(childId({ ...row, ...changes } as EngineSignalRow)).not.toBe(childId(row));
  });
  it('keeps an unchanged plan stable through quote refreshes', () => {
    expect(childId({ ...row, spot: 1901, legs: [{ ...leg, premium_spot: 42 }] })).toBe(childId(row));
  });
});

it('keeps notification identity independent of row order and candle-close display time',()=>{
  const [a]=supertrendToBoard([row]);
  const b=supertrendToBoard([{...row,underlying:'OTHER'},row])[1];
  expect(a.notificationId).toBe(b.notificationId);
  expect(a.sourceTimestampMs).toBe(row.timestamp_ms);
  expect(a.notificationId).toBe(`supertrend:${row.token}:${row.timestamp_ms}:derivatives:swing:60`);
  expect(supertrendToBoard([{...row,horizon_mode:'scalping',signal_timeframe_minutes:5}])[0].notificationId).not.toBe(a.notificationId);
});

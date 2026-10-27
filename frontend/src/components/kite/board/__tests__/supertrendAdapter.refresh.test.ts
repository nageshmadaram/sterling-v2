import { describe, expect, it } from 'vitest';
import { legSafetyTags, supertrendLegToBoard } from '../supertrendAdapter';
import type { EngineSignalRow, OptionLeg, ScanRefreshState } from '../../../../types/kiteEngine';
const leg = { option_symbol: 'A-CE', option_type: 'CE', moneyness: 'ATM', strike: 100,
  expiry: '2026-12-31', premium_spot: 100, entry_sl: 90, is_active: true,
  signal_timestamp_ms: Date.UTC(2026, 9, 26, 4, 45) } as OptionLeg;
const row = { underlying: 'A', exchange: 'NFO', source: 'derivatives', horizon_mode: 'swing',
  timestamp_ms: leg.signal_timestamp_ms, legs: [leg], is_active: true } as EngineSignalRow;
describe('last-known refresh status', () => {
  it.each<ScanRefreshState>(['pending', 'failed', 'replaced'])('blocks %s contract without hiding its plan', state => {
    const stale = { ...leg, scan_refresh_state: state };
    expect(supertrendLegToBoard(row, stale, 0).executionEligible).toBe(false);
    expect(legSafetyTags(row, stale, false)[0].hint).toMatch(/entries are blocked/);
    expect(supertrendLegToBoard(row, stale, 0).levels.entry).toBe(100);
  });
  it('allows a verified sibling while retaining the other leg as read only', () => {
    expect(supertrendLegToBoard(row, leg, 0).executionEligible).toBe(true);
    expect(supertrendLegToBoard(row, { ...leg, scan_refresh_state: 'pending' }, 0).executionEligible).toBe(false);
  });
});

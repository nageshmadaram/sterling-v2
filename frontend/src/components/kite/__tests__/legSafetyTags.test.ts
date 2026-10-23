import { describe, expect, it } from 'vitest';
import { legSafetyTags } from '../SterlingKiteEnginePane';

const soon = new Date(Date.now() + 3 * 86_400_000).toISOString().slice(0, 10);
const later = new Date(Date.now() + 40 * 86_400_000).toISOString().slice(0, 10);
const row = (over: Record<string, unknown> = {}) => ({ horizon_mode: 'swing', ...over }) as any;
const labels = (r: any, leg: any, ended = false) => legSafetyTags(r, leg, ended).map((t) => t.label);

describe('legSafetyTags', () => {
  it('passes a valid swing leg on a later expiry', () => {
    expect(labels(row(), { premium_spot: 10, entry_sl: 7, expiry: later })).toEqual([]);
  });
  it('flags a missing stop and a stop at or above entry', () => {
    expect(labels(row(), { premium_spot: 9, entry_sl: null, premium_sl: null, expiry: later })).toContain('NO STOP');
    expect(labels(row(), { premium_spot: 2.10, entry_sl: 2.18, expiry: later })).toContain('STOP ≥ ENTRY');
  });
  it('flags a swing leg expiring before the planned hold', () => {
    expect(labels(row(), { premium_spot: 10, entry_sl: 7, expiry: soon })).toContain('EXPIRES SOON');
    expect(labels(row({ horizon_mode: 'intraday' }), { premium_spot: 10, entry_sl: 7, expiry: soon })).not.toContain('EXPIRES SOON');
  });
  it('flags replaced and view-only legs; a closed leg keeps only view-only', () => {
    expect(labels(row(), { premium_spot: 10, entry_sl: 7, expiry: later, replaced: true })).toContain('REPLACED');
    expect(labels(row({ view_only: true, horizon_mode: 'scalping' }), { premium_spot: 10, entry_sl: null }, true)).toEqual(['VIEW ONLY · Scalp']);
  });
});

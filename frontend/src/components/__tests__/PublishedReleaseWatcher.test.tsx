import { describe, expect, it } from 'vitest';
import {
  isNewPublishedRelease,
  canAutoRefreshPublishedRelease,
} from '../PublishedReleaseWatcher';

const old = '8fa98e3f879491f9914c5da04af45ecefcb13ddb';
const next = 'a164fcd8014a533df4b47a2edb2abae6c19dffa1';

describe('published build identity', () => {
  it('recognizes a newly deployed hash, not a newer GitHub branch pointer', () => {
    expect(isNewPublishedRelease(old, next)).toBe(true);
    expect(isNewPublishedRelease(next, old)).toBe(true);
    // The publisher decides which SHA is served. This tests only mismatch,
    // never assumes a SHA's ordering implies promotion.
    expect(isNewPublishedRelease(old, old)).toBe(false);
    expect(isNewPublishedRelease('UNKNOWN', next)).toBe(false);
    expect(isNewPublishedRelease(old, null)).toBe(false);
    expect(isNewPublishedRelease(old, 'deadbeef')).toBe(false);
    expect(isNewPublishedRelease(old, 'https://evil.invalid/')).toBe(false);
    expect(isNewPublishedRelease(old, '../secrets')).toBe(false);
  });

  it('never silently reloads a trading draft, modal, TV or Radio', () => {
    expect(canAutoRefreshPublishedRelease('/kite', false, false)).toBe(true);
    expect(canAutoRefreshPublishedRelease('/kite', true, false)).toBe(false);
    expect(canAutoRefreshPublishedRelease('/kite', false, true)).toBe(false);
    expect(canAutoRefreshPublishedRelease('/radio', false, false)).toBe(false);
    expect(canAutoRefreshPublishedRelease('/tv', false, false)).toBe(false);
    expect(canAutoRefreshPublishedRelease('/', false, false)).toBe(true);
  });
});

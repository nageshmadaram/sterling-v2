import { describe, expect, it } from 'vitest';
import { snapbackSnapshotPollMs } from '../useSnapback';

describe('Snapback live snapshot refresh priority', () => {
  it('does not defer an idle Swing snapshot for thirty seconds', () => {
    expect(snapbackSnapshotPollMs(false)).toBe(5_000);
    expect(snapbackSnapshotPollMs(false, false)).toBe(5_000);
  });

  it('refreshes partial rows more frequently during an active scan', () => {
    expect(snapbackSnapshotPollMs(false, true)).toBe(1_500);
  });

  it('keeps the fast replay cadence independent of scan status', () => {
    expect(snapbackSnapshotPollMs(true)).toBe(1_000);
    expect(snapbackSnapshotPollMs(true, true)).toBe(1_000);
  });
});

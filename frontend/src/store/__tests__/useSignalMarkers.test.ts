import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSignalMarkers } from '../useSignalMarkers';

beforeEach(() => useSignalMarkers.setState({ byRow: {}, markers: {} }));

describe('atomic marker updates', () => {
  it('publishes a large board with one notification and no notification on identical refresh', () => {
    const notify = vi.fn();
    const stop = useSignalMarkers.subscribe(notify);
    try {
      const rows = Object.fromEntries(Array.from({ length: 300 }, (_, i) => [`row:${i}`, { [`NFO:OPT${i}`]: { delta: true } }]));
      useSignalMarkers.getState().replaceRows([], rows);
      expect(notify).toHaveBeenCalledTimes(1);
      const state = useSignalMarkers.getState();
      useSignalMarkers.getState().replaceRows(Object.keys(rows), structuredClone(rows));
      expect(notify).toHaveBeenCalledTimes(1);
      expect(useSignalMarkers.getState()).toBe(state);
    } finally { stop(); }
  });

  it('replaces only owned rows without losing markers from another board', () => {
    const store = useSignalMarkers.getState();
    store.publish('other', { 'NFO:SHARED': { rr: true } });
    store.replaceRows([], { mine: { 'NFO:SHARED': { delta: true } } });
    expect(useSignalMarkers.getState().markers['NFO:SHARED']).toEqual({ rr: true, delta: true });
    const snapshots: unknown[] = [];
    const stop = useSignalMarkers.subscribe(s => snapshots.push(s.markers));
    try {
      store.replaceRows(['mine'], { replacement: { 'NFO:SHARED': { delta: true } } });
      expect(snapshots).toHaveLength(1);
      expect(useSignalMarkers.getState().markers['NFO:SHARED']).toEqual({ rr: true, delta: true });
      store.replaceRows(['replacement'], {});
      expect(useSignalMarkers.getState().byRow.other).toBeDefined();
      expect(useSignalMarkers.getState().markers['NFO:SHARED']?.rr).toBe(true);
      expect(useSignalMarkers.getState().markers['NFO:SHARED']?.delta).toBeFalsy();
    } finally { stop(); }
  });

  it('retains aggregate references when ownership changes but displayed markers do not', () => {
    const store = useSignalMarkers.getState();
    store.replaceRows([], { old: { A: { delta: true } } });
    const markers = useSignalMarkers.getState().markers;
    store.replaceRows(['old'], { next: { A: { delta: true } } });
    expect(useSignalMarkers.getState().markers).toBe(markers);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The view (LIVE or SIMULATION) is remembered per browser. It decides which tenant every
 * request reads and writes, so a bad stored value must never silently pick Simulation, and a
 * browser that refuses storage must still work.
 */
async function freshStore() {
  vi.resetModules();
  return import('../useViewStore');
}

beforeEach(() => { localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe('useViewStore', () => {
  it('defaults to live', async () => {
    const { useViewStore, getView } = await freshStore();
    expect(useViewStore.getState().view).toBe('live');
    expect(getView()).toBe('live');
  });

  it('remembers the choice in this browser and restores it on the next load', async () => {
    const first = await freshStore();
    first.useViewStore.getState().setView('simulation');
    expect(localStorage.getItem('sterling.view')).toBe('simulation');

    const second = await freshStore();
    expect(second.getView()).toBe('simulation');

    second.useViewStore.getState().setView('live');
    const third = await freshStore();
    expect(third.getView()).toBe('live');
  });

  it('treats anything but "simulation" in storage as live', async () => {
    localStorage.setItem('sterling.view', 'SIMULATION ');
    expect((await freshStore()).getView()).toBe('live');
    localStorage.setItem('sterling.view', 'paper');
    expect((await freshStore()).getView()).toBe('live');
  });

  it('still works when storage throws on read and on write', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    const { useViewStore, getView } = await freshStore();
    expect(getView()).toBe('live');
    expect(() => useViewStore.getState().setView('simulation')).not.toThrow();
    expect(getView()).toBe('simulation');
  });

  it('does not notify when the view is unchanged', async () => {
    const { useViewStore } = await freshStore();
    const listener = vi.fn();
    useViewStore.subscribe(listener);
    useViewStore.getState().setView('live');
    expect(listener).not.toHaveBeenCalled();
    useViewStore.getState().setView('simulation');
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('withViewQuery', () => {
  it('leaves the live socket URL alone', async () => {
    const { withViewQuery } = await freshStore();
    expect(withViewQuery('ws://h/api/v1/stream/ws', 'live')).toBe('ws://h/api/v1/stream/ws');
  });

  it('adds ?view=simulation, or &view=simulation after an existing query', async () => {
    const { withViewQuery } = await freshStore();
    expect(withViewQuery('ws://h/api/v1/stream/ws', 'simulation')).toBe('ws://h/api/v1/stream/ws?view=simulation');
    expect(withViewQuery('ws://h/s?x=1', 'simulation')).toBe('ws://h/s?x=1&view=simulation');
  });

  it('reads the current view when none is given', async () => {
    const { withViewQuery, useViewStore } = await freshStore();
    useViewStore.getState().setView('simulation');
    expect(withViewQuery('/s')).toBe('/s?view=simulation');
  });
});

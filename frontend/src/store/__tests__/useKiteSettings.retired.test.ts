import { describe, it, expect } from 'vitest';
import { ENGINE_ORDER, useKiteSettings } from '../useKiteSettings';

/** Settings saved while retired strategies existed must not point at one. */
describe('kite settings migration drops retired strategies', () => {
  const migrate = (persisted: Record<string, unknown>) =>
    (useKiteSettings.persist.getOptions().migrate as (p: unknown, v: number) => Record<string, unknown>)(persisted, 10);

  it('resets a default page that no longer exists', () => {
    expect(migrate({ defaultSection: 'openingLeaders' }).defaultSection).toBe('dashboard');
    expect(migrate({ defaultSection: 'adaptiveEdge' }).defaultSection).toBe('dashboard');
    expect(migrate({ defaultSection: 'positions' }).defaultSection).toBe('positions');
  });

  it('resets a default engine that is retired', () => {
    expect(migrate({ defaultSignalEngine: 'adaptive_edge' }).defaultSignalEngine).toBe('supertrend');
    expect(migrate({ defaultSignalEngine: 'snapback' }).defaultSignalEngine).toBe('snapback');
  });

  it('drops retired engines from a saved tab order and keeps the rest', () => {
    const out = migrate({ engineOrder: ['intraday', 'snapback', 'adaptive_edge', 'supertrend'] });
    expect(out.engineOrder).toEqual(['snapback', 'supertrend', 'gamma_move', 'ema_trend']);
    for (const id of out.engineOrder as string[]) expect(ENGINE_ORDER).toContain(id);
  });
});

it('removes the retired renderer choice without resetting capabilities', () => {
  const migrate = useKiteSettings.persist.getOptions().migrate!;
  const state = migrate({ boardRenderer: 'classic', boardRowScroll: false, boardRowActions: false }, 13) as any;
  expect(state).not.toHaveProperty('boardRenderer');
  expect(state.boardRowScroll).toBe(false);
  expect(state.boardRowActions).toBe(false);
  expect(useKiteSettings.getState()).not.toHaveProperty('setBoardRenderer');
});

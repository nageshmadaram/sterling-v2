import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

const frontend = process.cwd();
const root = resolve(frontend, '..');
const noZoom = readFileSync(resolve(frontend, 'public/media/mobile-no-zoom.js'), 'utf8');

type Gesture = {
  cancelable: boolean;
  touches?: unknown[];
  ctrlKey?: boolean;
  preventDefault(): void;
};

function harness(mobile: boolean) {
  const listeners = new Map<string, {
    handler: (event: Gesture) => void;
    options: { capture?: boolean; passive?: boolean };
  }>();
  const context = {
    window: { matchMedia: (query: string) => ({
      matches: mobile && query === '(max-width: 1023px)',
    }) },
    document: { addEventListener: (
      event: string,
      handler: (event: Gesture) => void,
      options: { capture?: boolean; passive?: boolean },
    ) => listeners.set(event, { handler, options }) },
  };
  runInNewContext(noZoom, context);

  function dispatch(type: string, data: Partial<Gesture> = {}) {
    let prevented = false;
    const event = {
      cancelable: true,
      preventDefault: () => { prevented = true; },
      ...data,
    };
    const listener = listeners.get(type);
    if (!listener) throw new Error(`Missing ${type} listener`);
    listener.handler(event);
    return prevented;
  }
  return { dispatch, listeners };
}

describe('mobile TV and Radio zoom isolation', () => {
  it.each(['tv', 'radio'] as const)(
    '%s uses a fixed-scale viewport and the shared media-only gesture policy',
    (kind) => {
      const html = readFileSync(resolve(root, `backend/app/media_templates/${kind}.html`), 'utf8');
      expect(html).toContain('<script src="/media/mobile-no-zoom.js"></script>');
      expect(html).toContain('maximum-scale=1, user-scalable=no');
      expect(html.indexOf('/media/mobile-no-zoom.js')).toBeLessThan(html.indexOf('</head>'));
    },
  );

  it('blocks multi-touch pinch on mobile but keeps one-finger scrolling', () => {
    const app = harness(true);
    expect(app.dispatch('touchstart', { touches: [{}] })).toBe(false);
    expect(app.dispatch('touchmove', { touches: [{}] })).toBe(false);
    expect(app.dispatch('touchstart', { touches: [{}, {}] })).toBe(true);
    expect(app.dispatch('touchmove', { touches: [{}, {}] })).toBe(true);
  });

  it('blocks iOS gesture zoom and double-tap defaults but not normal wheel', () => {
    const app = harness(true);
    expect(app.dispatch('gesturestart')).toBe(true);
    expect(app.dispatch('gesturechange')).toBe(true);
    expect(app.dispatch('gestureend')).toBe(true);
    expect(app.dispatch('dblclick')).toBe(true);
    expect(app.dispatch('wheel', { ctrlKey: true })).toBe(true);
    expect(app.dispatch('wheel', { ctrlKey: false })).toBe(false);
  });

  it('preserves desktop interactions and noncancelable events', () => {
    const desktop = harness(false);
    expect(desktop.dispatch('touchstart', { touches: [{}, {}] })).toBe(false);
    expect(desktop.dispatch('gesturestart')).toBe(false);
    expect(desktop.dispatch('dblclick')).toBe(false);
    expect(desktop.dispatch('wheel', { ctrlKey: true })).toBe(false);

    const mobile = harness(true);
    expect(mobile.dispatch('touchstart', { touches: [{}, {}], cancelable: false })).toBe(false);
    for (const type of ['touchstart', 'touchmove', 'gesturestart', 'gesturechange', 'gestureend', 'dblclick', 'wheel']) {
      expect(mobile.listeners.get(type)?.options).toEqual({ capture: true, passive: false });
    }
  });
});

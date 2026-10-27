import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

const workerSource = readFileSync(resolve(process.cwd(), 'public/sw.js'), 'utf8');

function mockWorker() {
  const handlers = new Map<string, (event: any) => void>();
  const focus = vi.fn(async () => undefined);
  const navigate = vi.fn(async (url: string) => ({
    url, focus,
  }));
  const openWindow = vi.fn(async (_url: string) => undefined);
  const showNotification = vi.fn(async (_title: string, _options: unknown) => undefined);
  const context: Record<string, any> = {
    self: {
      registration: {
        scope: 'https://sterling.example/',
        showNotification,
      },
      clients: {
        claim: vi.fn(),
        matchAll: vi.fn(async () => [{url: 'https://sterling.example/', navigate, focus}]),
        openWindow,
      },
      addEventListener(type: string, handler: (event: any) => void) {
        handlers.set(type, handler);
      },
      skipWaiting: vi.fn(),
    },
    URL, URLSearchParams,
  };
  runInNewContext(workerSource, context);
  return { handlers, context, focus, navigate, openWindow, showNotification };
}

describe('mobile Web Push navigation', () => {
  it('preserves signal detail and rejects off-site or broken destinations', () => {
    const { context } = mockWorker();
    const link = context.safeDestination(
      '/?view=live&notification=signals&strategy=snapback&signal=SB%3A123&at=1793160000000',
    );
    expect(link).toBe(
      'https://sterling.example/kite?view=live&notification=signals&strategy=snapback&signal=SB%3A123&at=1793160000000',
    );
    expect(context.safeDestination('https://attacker.example/steal')).toBe('https://sterling.example/');
    expect(context.safeDestination('/missing?notification=signals')).toBe('https://sterling.example/');
    expect(context.safeDestination('/?notification=unknown')).toBe('https://sterling.example/');
  });

  it('routes a click by navigating the Sterling window before focusing it', async () => {
    const w = mockWorker();
    let work: Promise<unknown> = Promise.resolve();
    w.handlers.get('notificationclick')!({
      notification: {
        data: {url: '/?notification=signals&view=live&strategy=gamma_move&signal=gamma-1'},
        close: vi.fn(),
      },
      waitUntil(p: Promise<unknown>) { work = p; },
    });
    await work;
    expect(w.navigate).toHaveBeenCalledWith(
      'https://sterling.example/kite?view=live&notification=signals&strategy=gamma_move&signal=gamma-1',
    );
    expect(w.focus).toHaveBeenCalledOnce();
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it('displays the supplied detailed title and message', async () => {
    const w = mockWorker();
    let work: Promise<unknown> = Promise.resolve();
    w.handlers.get('push')!({
      data: {json: () => ({title: 'Snapback · ABC', body: 'LONG · 25000 CE · entry blocked',
        url: '/?notification=signals&view=live&strategy=snapback'})},
      waitUntil(p: Promise<unknown>) { work = p; },
    });
    await work;
    expect(w.showNotification).toHaveBeenCalledWith(
      'Snapback · ABC', expect.objectContaining({body: 'LONG · 25000 CE · entry blocked'}),
    );
  });
});

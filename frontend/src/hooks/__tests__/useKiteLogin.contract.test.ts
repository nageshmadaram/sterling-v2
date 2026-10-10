import { describe, expect, it } from 'vitest';
import useKiteSource from '../useKite.ts?raw';

describe('Kite login app-preservation contract', () => {
  it('never replaces the Sterling tab when the login popup is blocked', () => {
    expect(useKiteSource).not.toMatch(/window\.location\.href\s*=\s*d\.login_url/);
    expect(useKiteSource).toMatch(/Kite login popup was blocked/);
    expect(useKiteSource).toMatch(/Allow popups for Sterling and try again/);
  });

  it('navigates only the dedicated popup to the broker login URL', () => {
    expect(useKiteSource).toMatch(/win\.location\.href\s*=\s*d\.login_url/);
  });
});

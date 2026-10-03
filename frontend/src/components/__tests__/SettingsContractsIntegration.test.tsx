import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import gammaPayload from './fixtures/gamma-move.json';
import { GammaMoveSettingsPanel } from '../kite/GammaMoveSettingsPanel';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => gammaPayload,
    text: async () => JSON.stringify(gammaPayload),
    headers: new Headers({ 'content-type': 'application/json' }),
  }) as Response));
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GammaMoveSettingsPanel />
    </QueryClientProvider>,
  );
}

async function settled() {
  await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
}

function openSection(re: RegExp) {
  const summary = [...document.querySelectorAll('summary')]
    .find((el) => re.test(el.textContent ?? ''));
  return summary?.closest('details') as HTMLDetailsElement | null;
}

describe('Gamma Unwind settings over the real API payload', () => {
  it('shows the reference expiry-list switch instead of the removed generic DTE window', async () => {
    mount();
    await settled();
    const text = document.body.textContent ?? '';
    expect(text).toContain('Expiry switch threshold');
    expect(text).toContain('trading days');
    expect(text).not.toContain('Minimum days to expiry');
    expect(text).not.toContain('Maximum days to expiry');
  });

  it('keeps Instruments before Watchlist and strike', async () => {
    mount();
    await settled();
    const text = document.body.textContent ?? '';
    expect(text.indexOf('Instruments')).toBeGreaterThan(-1);
    expect(text.indexOf('Watchlist and strike')).toBeGreaterThan(text.indexOf('Instruments'));
  });

  it('shows the effective client HOI-1 and fixed 1m/5m/15m timeframes', async () => {
    mount();
    await settled();
    const text = document.body.textContent ?? '';
    expect(text).toContain('effective HOI-1');
    expect(text).toContain('1m base');
    expect(text).toContain('5m signal');
    expect(text).toContain('15m display');
  });

  it('opens the reference watchlist and watcher sections', async () => {
    mount();
    await settled();
    expect(openSection(/Watchlist and strike/)?.open).toBe(true);
    expect(openSection(/Watcher/)?.open).toBe(true);
  });
});

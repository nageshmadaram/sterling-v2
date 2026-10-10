/**
 * The two views are two books. Rows fetched for one must never be painted in the other, not
 * even for the instant a refetch is in flight (the app keeps the previous data on screen
 * while a new key loads, which is exactly how a live row would leak into Simulation).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { useViewStore } from '../../../store/useViewStore';
import { useKiteNotifications } from '../../../store/useKiteNotifications';
import { ViewQueryProvider, __resetViewClients } from '../ViewQueryProvider';

type Row = { symbol: string };
type Pending = { view: string; resolve: (rows: Row[]) => void };

let pending: Pending[];
let fetchMock: Mock<[string, RequestInit?], Promise<Response>>;

function Rows() {
  const q = useQuery<Row[]>({
    queryKey: ['kite-positions'],
    queryFn: () => api.get<Row[]>('/api/v1/kite/positions'),
  });
  return (
    <ul aria-label="rows" data-state={q.isFetching ? 'fetching' : 'idle'}>
      {(q.data ?? []).map((r) => <li key={r.symbol}>{r.symbol}</li>)}
    </ul>
  );
}

const app = () => <ViewQueryProvider><Rows /></ViewQueryProvider>;
const resolveNext = async (view: string, rows: Row[]) => {
  const idx = pending.findIndex((p) => p.view === view);
  expect(idx).toBeGreaterThanOrEqual(0);
  const [p] = pending.splice(idx, 1);
  await act(async () => { p.resolve(rows); });
};
const callsFor = (view: string) =>
  fetchMock.mock.calls.filter((c) => {
    const h = new Headers((c[1] as RequestInit | undefined)?.headers).get('X-Sterling-View') ?? 'live';
    return String(c[0]).includes('/positions') && h === view;
  });

beforeEach(() => {
  pending = [];
  __resetViewClients();
  useViewStore.setState({ view: 'live' });
  fetchMock = vi.fn((url: string, init?: RequestInit) => new Promise<Response>((resolve) => {
    const view = new Headers(init?.headers).get('X-Sterling-View') ?? 'live';
    pending.push({
      view,
      resolve: (rows) => resolve(new Response(JSON.stringify(rows), { status: 200, headers: { 'Content-Type': 'application/json' } })),
    });
  }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useViewStore.setState({ view: 'live' });
});

describe('ViewQueryProvider', () => {
  it('refetches on a switch and never shows the other view\'s rows, even while the new rows load', async () => {
    render(app());
    await resolveNext('live', [{ symbol: 'LIVE-NIFTY' }]);
    expect(await screen.findByText('LIVE-NIFTY')).toBeInTheDocument();

    act(() => { useViewStore.getState().setView('simulation'); });

    // The simulation request has been sent, with the header, and its rows have not arrived.
    await waitFor(() => expect(callsFor('simulation')).toHaveLength(1));
    expect(screen.queryByText('LIVE-NIFTY')).toBeNull();

    await resolveNext('simulation', [{ symbol: 'SIM-NIFTY' }]);
    expect(await screen.findByText('SIM-NIFTY')).toBeInTheDocument();
    expect(screen.queryByText('LIVE-NIFTY')).toBeNull();
  });

  it('goes back to live with a fresh fetch, and the virtual rows are gone at once', async () => {
    render(app());
    await resolveNext('live', [{ symbol: 'LIVE-NIFTY' }]);
    act(() => { useViewStore.getState().setView('simulation'); });
    await waitFor(() => expect(callsFor('simulation')).toHaveLength(1));
    await resolveNext('simulation', [{ symbol: 'SIM-NIFTY' }]);
    expect(await screen.findByText('SIM-NIFTY')).toBeInTheDocument();

    act(() => { useViewStore.getState().setView('live'); });
    await waitFor(() => expect(callsFor('live')).toHaveLength(2));
    expect(screen.queryByText('SIM-NIFTY')).toBeNull();
    // Not even the live rows from the first visit: the cache starts cold every time.
    expect(screen.queryByText('LIVE-NIFTY')).toBeNull();

    await resolveNext('live', [{ symbol: 'LIVE-BANKNIFTY' }]);
    expect(await screen.findByText('LIVE-BANKNIFTY')).toBeInTheDocument();
  });

  it('ignores a slow answer for the view that was left', async () => {
    render(app());
    // The live request is still in flight when the operator switches.
    await waitFor(() => expect(callsFor('live')).toHaveLength(1));
    act(() => { useViewStore.getState().setView('simulation'); });
    await waitFor(() => expect(callsFor('simulation')).toHaveLength(1));

    await resolveNext('live', [{ symbol: 'LIVE-LATE' }]);
    expect(screen.queryByText('LIVE-LATE')).toBeNull();

    await resolveNext('simulation', [{ symbol: 'SIM-NIFTY' }]);
    expect(await screen.findByText('SIM-NIFTY')).toBeInTheDocument();
    expect(screen.queryByText('LIVE-LATE')).toBeNull();
  });

  it('serves one view from one cache: a rerender in the same view does not refetch', async () => {
    const { rerender } = render(app());
    await resolveNext('live', [{ symbol: 'LIVE-NIFTY' }]);
    rerender(app());
    expect(callsFor('live')).toHaveLength(1);
    expect(await screen.findByText('LIVE-NIFTY')).toBeInTheDocument();
  });

  it('clears the toasts on a switch: a fill in one book is not news in the other', async () => {
    render(app());
    act(() => { useKiteNotifications.getState().push({ kind: 'complete', title: 'Order complete', message: 'NIFTY BUY' }); });
    expect(useKiteNotifications.getState().items).toHaveLength(1);
    act(() => { useViewStore.getState().setView('simulation'); });
    expect(useKiteNotifications.getState().items).toHaveLength(0);
  });
});

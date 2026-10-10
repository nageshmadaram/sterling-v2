import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useKiteOrderUpdates } from '../useKite';
import { useViewStore } from '../../store/useViewStore';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { MockWebSocket.instances.push(this); }
  send(d: string) { this.sent.push(JSON.parse(d)); }
  close() { this.onclose?.(); }
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  useViewStore.setState({ view: 'live' });
});
afterEach(() => {
  vi.unstubAllGlobals();
  useViewStore.setState({ view: 'live' });
});

describe('useKiteOrderUpdates follows the view', () => {
  it('listens on the owner\'s order channel in the live view', () => {
    renderHook(() => useKiteOrderUpdates(true), { wrapper });
    const ws = MockWebSocket.instances[0];
    expect(ws.url).not.toContain('view=');
    ws.onopen?.();
    expect(ws.sent).toEqual([{ action: 'subscribe', channel: 'kite_orders:default' }]);
  });

  it('listens on the virtual tenant\'s channel, with the view in the URL, in the Simulation view', () => {
    useViewStore.setState({ view: 'simulation' });
    renderHook(() => useKiteOrderUpdates(true), { wrapper });
    const ws = MockWebSocket.instances[0];
    expect(ws.url).toMatch(/\/api\/v1\/stream\/ws\?view=simulation$/);
    ws.onopen?.();
    expect(ws.sent).toEqual([{ action: 'subscribe', channel: 'kite_orders:sim:default' }]);
  });

  it('reconnects when the view changes', () => {
    renderHook(() => useKiteOrderUpdates(true), { wrapper });
    expect(MockWebSocket.instances).toHaveLength(1);
    act(() => { useViewStore.getState().setView('simulation'); });
    expect(MockWebSocket.instances.at(-1)?.url).toMatch(/view=simulation$/);
  });
});

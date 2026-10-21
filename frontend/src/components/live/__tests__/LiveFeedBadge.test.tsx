import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { LiveFeedBadge } from '../LiveFeedBadge';
import { CONNECTING_GRACE_MS, useStreamHealthStore } from '../../../store/useStreamHealthStore';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-05T09:30:00Z'));
  useStreamHealthStore.getState().reset();
});
afterEach(() => {
  useStreamHealthStore.getState().reset();
  vi.useRealTimers();
});

describe('LiveFeedBadge', () => {
  it('renders nothing while the stream is idle', () => {
    render(<LiveFeedBadge />);
    expect(screen.queryByTestId('live-feed-badge')).toBeNull();
  });

  it('shows a visible offline warning that says the data is not live', () => {
    render(<LiveFeedBadge />);
    act(() => {
      useStreamHealthStore.getState().setTransport('open');
      useStreamHealthStore.getState().setTransport('closed');
    });
    expect(screen.queryByTestId('live-feed-badge')).toBeNull();
    act(() => { vi.advanceTimersByTime(CONNECTING_GRACE_MS + 1_000); });
    const badge = screen.getByTestId('live-feed-badge');
    expect(badge.getAttribute('data-level')).toBe('down');
    expect(badge.textContent).toMatch(/offline/i);
    expect(badge.textContent).toMatch(/not live/i);
  });

  it('shows a broker-feed warning while the socket itself is fine', () => {
    render(<LiveFeedBadge />);
    act(() => {
      useStreamHealthStore.getState().setTransport('open');
      useStreamHealthStore.getState().setBroker({
        started: true, connected: false, lastTickMs: 1, lastError: 'ConnectionClosed', reportedAt: Date.now(),
      });
    });
    const badge = screen.getByTestId('live-feed-badge');
    expect(badge.getAttribute('data-level')).toBe('broker-down');
    expect(badge.textContent).toMatch(/broker feed down/i);
    expect(badge.textContent).toContain('ConnectionClosed');
  });

  it('disappears again when the feed recovers', () => {
    render(<LiveFeedBadge />);
    act(() => {
      useStreamHealthStore.getState().setTransport('open');
      useStreamHealthStore.getState().setTransport('closed');
    });
    expect(screen.queryByTestId('live-feed-badge')).toBeNull();
    act(() => { vi.advanceTimersByTime(CONNECTING_GRACE_MS + 1_000); });
    expect(screen.getByTestId('live-feed-badge')).toBeTruthy();
    act(() => { useStreamHealthStore.getState().setTransport('idle'); });
    expect(screen.queryByTestId('live-feed-badge')).toBeNull();
  });
});

it('dismisses by swipe without changing feed health and returns for a new outage',()=>{
  vi.stubGlobal('PointerEvent',MouseEvent);
  render(<LiveFeedBadge />);
  act(()=>{useStreamHealthStore.getState().setTransport('open');useStreamHealthStore.getState().setTransport('closed');vi.advanceTimersByTime(CONNECTING_GRACE_MS+1000);});
  const badge=screen.getByTestId('live-feed-badge');
  fireEvent.pointerDown(badge,{clientX:180,clientY:20});
  fireEvent.pointerUp(badge,{clientX:80,clientY:25});
  expect(screen.queryByTestId('live-feed-badge')).toBeNull();
  expect(useStreamHealthStore.getState().transport).toBe('closed');
  act(()=>{useStreamHealthStore.getState().setTransport('idle');});
  act(()=>{useStreamHealthStore.getState().setTransport('closed');vi.advanceTimersByTime(CONNECTING_GRACE_MS+1000);});
  expect(screen.getByTestId('live-feed-badge')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'Dismiss live feed warning'}));
  expect(screen.queryByTestId('live-feed-badge')).toBeNull();
  vi.unstubAllGlobals();
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { LiveFeedBadge } from '../LiveFeedBadge';
import { useStreamHealthStore } from '../../../store/useStreamHealthStore';

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
    expect(screen.getByTestId('live-feed-badge')).toBeTruthy();
    act(() => { useStreamHealthStore.getState().setTransport('idle'); });
    expect(screen.queryByTestId('live-feed-badge')).toBeNull();
  });
});

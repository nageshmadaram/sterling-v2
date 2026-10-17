import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScanCountdownRing } from '../ScanCountdownRing';

describe('isolated countdown animation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-17T09:30:00.000Z'));
  });
  afterEach(() => { vi.useRealTimers(); });

  it('advances the percentage without requiring the parent sidebar to rerender', () => {
    const start = Date.now();
    const props = { generatedMs: start, nextScanMs: start + 10_000, autoScan: true, scanning: false };
    const page = render(<ScanCountdownRing {...props} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(5_000); });
    expect(screen.getByText('50')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(5_000); });
    expect(screen.getByText('100')).toBeInTheDocument();
    page.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not create a polling interval when markets are closed or a scan is running', () => {
    const now = Date.now();
    const view = render(<ScanCountdownRing generatedMs={now} nextScanMs={now + 10_000}
      autoScan={false} scanning={false} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
    view.rerender(<ScanCountdownRing generatedMs={now} nextScanMs={now + 10_000}
      autoScan={true} scanning={true} />);
    expect(vi.getTimerCount()).toBe(0);
  });
});

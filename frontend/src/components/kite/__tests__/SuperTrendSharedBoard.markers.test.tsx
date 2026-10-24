import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SuperTrendSharedBoard } from '../SuperTrendSharedBoard';
import { useSignalMarkers } from '../../../store/useSignalMarkers';

vi.mock('../board/SignalBoard', () => ({ SignalBoard: () => null, DEFAULT_SORT: { column: 'time', direction: 'desc' } }));
vi.mock('../board/useBoardRowActions', () => ({ useBoardRowActions: () => ({}) }));
vi.mock('../board/useSignalColumns', () => ({ useSignalColumns: () => ({ columns: [], hidden: new Set() }) }));

const NOW = Date.parse('2026-10-23T11:00:00+05:30');
const select = vi.fn();
function row(token: number, mode = 'swing', minutes = 60): any {
  return {
    token, underlying: `STOCK${token}`, exchange: 'NFO', direction: 'long', option_type: 'CE', source: 'spot',
    timestamp_ms: NOW - 60000, horizon_mode: mode, signal_timeframe_minutes: minutes,
    regime: 'BULL', alignment: { fast: 1, mid: 1, slow: 1 }, spot: 100, stop_loss: 90,
    is_active: true, is_fresh: true, score: 80,
    legs: ['A', 'B'].map((suffix, i) => ({ option_symbol: `OPT${token}${suffix}`, option_type: 'CE',
      premium_spot: 100, premium_sl: 95, entry_sl: 90, premium_target: i ? 110 : 130,
      token: token * 10 + i, strike: 100, expiry: '2026-10-29', lot_size: 10, is_active: true })),
  };
}
beforeEach(() => useSignalMarkers.setState({ byRow: {}, markers: {} }));

describe('SuperTrend marker lifecycle', () => {
  it('does not describe non-hourly modes as hourly when today has no entries', () => {
    const old = { ...row(1, 'ultra_scalping', 1), timestamp_ms: NOW - 86400000 };
    render(<SuperTrendSharedBoard rows={[old]} nowMs={NOW} mode="ultra_scalping" onSelectSignal={select} />);
    expect(screen.getByText(/No new SuperTrend transitions today/)).toBeVisible();
    expect(screen.queryByText(/No new 1H/)).toBeNull();
  });
  it('does not remove or republish unchanged marks when only the clock advances', () => {
    const rows = [row(1), row(2), row(3)];
    const view = render(<SuperTrendSharedBoard rows={rows} nowMs={NOW} onSelectSignal={select} />);
    expect(Object.keys(useSignalMarkers.getState().markers)).toHaveLength(3);
    const notify = vi.fn(); const stop = useSignalMarkers.subscribe(notify);
    try {
      view.rerender(<SuperTrendSharedBoard rows={rows} nowMs={NOW + 1000} onSelectSignal={select} />);
      expect(notify).not.toHaveBeenCalled();
    } finally { stop(); }
  });

  it('removes a vanished row atomically without blanking retained rows', () => {
    const rows = [row(1), row(2)];
    const view = render(<SuperTrendSharedBoard rows={rows} nowMs={NOW} onSelectSignal={select} />);
    const snapshots: unknown[] = []; const stop = useSignalMarkers.subscribe(s => snapshots.push(s.markers));
    try {
      view.rerender(<SuperTrendSharedBoard rows={[rows[0]]} nowMs={NOW} onSelectSignal={select} />);
      expect(snapshots).toHaveLength(1);
      expect(useSignalMarkers.getState().markers['NFO:OPT1A']?.rr).toBe(true);
      expect(useSignalMarkers.getState().markers['NFO:OPT2A']).toBeUndefined();
    } finally { stop(); }
  });

  it('keeps simultaneous modes and timeframes in distinct marker slots', () => {
    const rows = [row(1, 'scalping', 1), row(1, 'scalping', 3), row(1, 'swing', 60)];
    render(<SuperTrendSharedBoard rows={rows} nowMs={NOW} onSelectSignal={select} />);
    expect(Object.keys(useSignalMarkers.getState().byRow)).toHaveLength(3);
  });

  it('unmounting one board preserves the other board’s identical signal marks', () => {
    const rows = [row(1)];
    const first = render(<SuperTrendSharedBoard rows={rows} nowMs={NOW} onSelectSignal={select} />);
    const second = render(<SuperTrendSharedBoard rows={rows} nowMs={NOW} onSelectSignal={select} />);
    first.unmount();
    expect(useSignalMarkers.getState().markers['NFO:OPT1A']?.rr).toBe(true);
    second.unmount();
    expect(useSignalMarkers.getState().markers).toEqual({});
  });
});

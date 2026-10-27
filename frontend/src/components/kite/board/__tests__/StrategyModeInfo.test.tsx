import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StrategyModeInfo } from '../StrategyModeInfo';

describe('mode explanations', () => {
  it('identifies the original hourly HA default separately from the selected mode', () => {
    render(<StrategyModeInfo engine="supertrend" st={{ horizon_mode: 'ultra_scalping' }} />);
    expect(screen.getByText(/Selected: Ultra scalp · 1-minute Heikin-Ashi/)).toBeVisible();
    expect(screen.getByText(/Default: Swing · original · 60-minute Heikin-Ashi/)).toBeVisible();
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText(/no separate hourly trading mode/)).toBeVisible();
    expect(screen.getAllByText(/Three aligned SuperTrends on a completed Heikin-Ashi candle/).length).toBe(5);
    expect(screen.getByRole('cell', { name: '20 minutes; same-day exit' })).toBeVisible();
  });
  it('makes per-mode entry rules and the next-session Swing timing visible in settings', () => {
    render(<StrategyModeInfo engine="snapback" sb={{ trading_mode: 'swing' }} />);
    expect(screen.getByText(/planned next-session entry/)).toBeVisible();
    expect(screen.getByText(/Swing triggers at end-of-day/)).toBeVisible();
    expect(screen.getAllByText(/Completed intraday candle reverses inside Bollinger band/).length).toBe(4);
  });
  it('shows effective saved or draft overrides rather than the preset', () => {
    render(<StrategyModeInfo engine="supertrend" st={{ horizon_mode: 'scalping', signal_timeframe_minutes: 3 }} />);
    expect(screen.getByText(/Selected: Scalp · 3-minute Heikin-Ashi/)).toBeVisible();
    expect(screen.getByText('Board mode filters only change rows shown. Settings determine candle size; each row or held position may retain its own mode.')).toBeVisible();
  });
  it('keeps dormant minute settings out of daily Snapback', () => {
    render(<StrategyModeInfo engine="snapback" sb={{ trading_mode: 'swing', scalp_timeframe_minutes: 3 }} />);
    expect(screen.getByText(/Selected: Swing · Daily regular OHLC/)).toBeVisible();
    expect(screen.queryByText(/Selected:.*3-minute/)).toBeNull();
  });
  it('shows Snapback custom bars and premium management', () => {
    render(<StrategyModeInfo engine="snapback" sb={{ trading_mode: 'intraday', scalp_timeframe_minutes: 5, scalp_target_points: 7, scalp_max_hold_bars: 12 }} />);
    expect(screen.getByText(/Selected: Intraday · 5-minute regular OHLC/)).toBeVisible();
    expect(screen.getByText(/Target 7 pts/)).toBeVisible();
    expect(screen.getByText(/Initial hold 12 bars/)).toBeVisible();
  });
  it.each(['gamma_move', 'ema_trend'])('keeps %s original only', engine => {
    render(<StrategyModeInfo engine={engine} />);
    expect(screen.getByText('Default · Original only')).toBeVisible();
    expect(screen.queryByText('Compare modes and configuration')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('keeps board configuration detail collapsed until requested', () => {
    render(<StrategyModeInfo engine="supertrend" bounded st={{ horizon_mode: 'swing' }} />);
    expect(screen.getByText(/Selected: Swing · 60-minute Heikin-Ashi/)).toBeVisible();
    expect(screen.getByText(/Three SuperTrends:/)).not.toBeVisible();
    fireEvent.click(screen.getByText('Compare modes and configuration'));
    expect(screen.getByText(/Three SuperTrends:/)).toBeVisible();
  });
  it('does not invent a loaded SuperTrend configuration', () => {
    render(<StrategyModeInfo engine="supertrend" />);
    expect(screen.getByText(/Selected configuration unavailable/)).toBeVisible();
  });
});

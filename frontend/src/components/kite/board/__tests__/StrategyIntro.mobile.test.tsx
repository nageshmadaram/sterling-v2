import React from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { StrategyIntro, strategyIntroSummary } from '../StrategyIntro';

beforeEach(() => localStorage.clear());
afterEach(cleanup);

const ALL_ENGINES = [
  'supertrend', 'snapback', 'gamma_move', 'ema_trend',
  'adaptive_edge', 'intraday', 'navigator', 'price_rules',
] as const;

describe('all strategy mobile overview rows', () => {
  it.each(ALL_ENGINES)('%s renders a single collapsed-by-default, expandable summary', engine => {
    render(<StrategyIntro engine={engine} />);
    const button = screen.getByRole('region', { name: 'Strategy overview' })
      .querySelector<HTMLButtonElement>('.strategy-mobile-overview-trigger');
    expect(button).not.toBeNull();
    if (!button) throw new Error('Missing strategy summary control');
    const overview = screen.getByRole('region', { name: 'Strategy overview' });
    expect(overview).not.toHaveClass('is-expanded');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).toHaveAttribute('aria-controls');
    expect(button.querySelector('.strategy-mobile-overview-title')).toHaveClass('strategy-mobile-overview-title');
    fireEvent.click(button);
    expect(screen.getByRole('region', { name: 'Strategy overview' })).toHaveClass('is-expanded');
    expect(button).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'false');
  });

  it('does not carry expanded SuperTrend help into a newly selected strategy', () => {
    const { rerender } = render(<StrategyIntro key="supertrend" engine="supertrend"
      st={{ horizon_mode:'swing',signal_timeframe_minutes:60 }}/>);
    fireEvent.click(screen.getByRole('button', { name: /SuperTrend/ }));
    expect(screen.getByRole('region', { name: 'Strategy overview' })).toHaveClass('is-expanded');
    rerender(<StrategyIntro key="gamma_move" engine="gamma_move" />);
    const next = screen.getByRole('button', { name: /Gamma Unwind/ });
    expect(next).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('region', { name: 'Strategy overview' })).not.toHaveClass('is-expanded');
  });

  it('keeps configured candle time and selected mode accurate in compact summary', () => {
    expect(strategyIntroSummary('supertrend', {
      horizon_mode: 'ultra_scalping', signal_timeframe_minutes: 1,
    })).toContain('Ultra scalp selected · 1m Heikin-Ashi');
    expect(strategyIntroSummary('supertrend', {
      horizon_mode: 'scalping', signal_timeframe_minutes: 3,
    })).toContain('Scalp selected · 3m Heikin-Ashi');
    expect(strategyIntroSummary('snapback', undefined, {
      trading_mode: 'intraday', scalp_timeframe_minutes: 5,
    })).toContain('Intraday selected · 5m OHLC');
    expect(strategyIntroSummary('snapback', undefined, {
      trading_mode: 'swing',
    })).toContain('Swing selected · Daily OHLC');
  });

  it('does not conceal practice-only warnings from a collapsed summary', () => {
    for (const engine of ['ema_trend', 'adaptive_edge']) {
      expect(strategyIntroSummary(engine)).toContain('Practice only');
    }
  });

  it('keeps full safety text and mode conditions after user expands', () => {
    render(<StrategyIntro engine="ema_trend" />);
    fireEvent.click(screen.getByRole('button', { name: /EMA Trend/ }));
    expect(screen.getByText('Practice only — no real orders')).toBeInTheDocument();
    expect(screen.getByText(/Real orders and automatic live trading are unavailable/)).toBeInTheDocument();
  });

  it('leaves price rules as a read-only disclosure with no arming action', () => {
    render(<StrategyIntro engine="price_rules" />);
    fireEvent.click(screen.getByRole('button', { name: /Price Rules/ }));
    expect(screen.getByText(/never arms or triggers an entry/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /buy|arm|execute/i })).not.toBeInTheDocument();
  });
});

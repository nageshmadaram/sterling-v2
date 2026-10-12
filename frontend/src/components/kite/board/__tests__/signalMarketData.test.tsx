import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { enrichSignalMarketData, useSignalMarketData } from '../useSignalMarketData';
import { SignalBoard } from '../SignalBoard';
import type { BoardSignal } from '../boardTypes';

const quoteHook = vi.hoisted(() => vi.fn(() => ({ data: {} })));
vi.mock('../../../../hooks/useKite', () => ({ useKiteQuote: quoteHook }));
const signal = (engine: 'snapback' | 'gamma_move', historical = false): BoardSignal => ({
  id: engine, engine, historical, underlying: 'NIFTY', direction: 'short', status: 'watching',
  instrument: { symbol: 'NIFTY26OCT25000PE', exchange: 'NFO', kind: 'option', quoteKey: 'NFO:NIFTY26OCT25000PE' },
  atMs: 1, levels: { entry: 100, ltp: null, stop: 80, trail: null, target: 140, exit: null },
  sizing: { quantity: 75, lots: 1, atRiskInr: 1500, deployedInr: 7500 },
  score: null, reason: 'Market closed', executionEligible: false, sections: [],
});
const quotes = { 'NFO:NIFTY26OCT25000PE': { last_price: 110, ohlc: { close: 100, open: 105 } } };

describe('live signal quote enrichment', () => {
  it('computes the same live option delta as SuperTrend from dated identity and real spot', () => {
    const original = signal('gamma_move');
    original.instrument = { ...original.instrument, strike: 25000, expiry: '2099-10-27', optionType: 'PE' };
    const enriched = enrichSignalMarketData([original], {
      ...quotes, 'NSE:NIFTY 50': { last_price: 25000 },
      'NFO:NIFTY26OCT25000PE': { last_price: 110, implied_volatility: 20 },
    }, 'close')[0];
    expect(enriched.delta).toBeTypeOf('number');
    expect(enriched.delta).toBeLessThan(0);
    expect(enrichSignalMarketData([original], quotes, 'close')[0].delta).toBeUndefined();
  });
  it.each(['snapback', 'gamma_move'] as const)('renders available %s measurements without altering plan authority', engine => {
    const original = signal(engine);
    const enriched = enrichSignalMarketData([original], quotes, 'close')[0];
    expect(enriched.levels).toEqual({ ...original.levels, ltp: 110 });
    expect(enriched.executionEligible).toBe(false);
    expect(enriched.sizing).toEqual(original.sizing);
    render(<SignalBoard signals={[enriched]} columns={['instrument', 'entry', 'stop', 'trail', 'exitState', 'target', 'chg', 'chgPct', 'dir', 'ltp']} hidden={new Set()} openId={null} onToggle={() => {}} nowMs={1} />);
    expect(screen.getByText('110.00')).toBeInTheDocument();
    expect(screen.getByText('10.00%')).toBeInTheDocument();
    expect(screen.getByText('▲')).toBeInTheDocument();
    expect(screen.getByText('80.00')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });
  it('honors the shared open basis and leaves unknown quotes missing', () => {
    expect(enrichSignalMarketData([signal('snapback')], quotes, 'open')[0].dayMove?.abs).toBe(5);
    expect(enrichSignalMarketData([signal('gamma_move')], {}, 'close')[0].levels.ltp).toBeNull();
  });
  it('never leaks present-day quotes into historical rows', () => {
    const past = signal('snapback', true);
    expect(enrichSignalMarketData([past], quotes, 'close')[0]).toBe(past);
  });
  it('subscribes only live identified contracts and makes no subscription in replay', () => {
    const rows = [signal('snapback'), { ...signal('gamma_move', true), instrument: { ...signal('gamma_move').instrument, quoteKey: 'NFO:HISTORY' } }];
    function Board({ replay }: { replay: boolean }) { useSignalMarketData(rows, replay); return null; }
    const { rerender } = render(<Board replay={false} />);
    expect(quoteHook).toHaveBeenLastCalledWith(['NFO:NIFTY26OCT25000PE', 'NSE:NIFTY 50'], true);
    rerender(<Board replay />);
    expect(quoteHook).toHaveBeenLastCalledWith([], false);
  });
});

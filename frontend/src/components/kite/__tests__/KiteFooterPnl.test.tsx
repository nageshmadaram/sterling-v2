import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { KiteFooterPnl } from '../KiteFooterPnl';

let mockPositionsData: any = {net: [], day: []};
let mockLtpData: any = {};
const spyPositions = vi.fn();
const spyLtp = vi.fn();
vi.mock('../../../hooks/useKite', () => ({
  useKitePositions: (...args: any[]) => {
    spyPositions(...args); return {data: mockPositionsData};
  },
  useKiteLtp: (...args: any[]) => {
    spyLtp(...args); return {data: mockLtpData};
  },
}));

beforeEach(() => {
  mockPositionsData = {net: [], day: []};
  mockLtpData = {};
  vi.clearAllMocks();
});

describe('isolated Kite footer P&L', () => {
  it('keeps the footer empty without positions', () => {
    render(<KiteFooterPnl onOpenPositions={() => {}} />);
    expect(screen.queryByRole('button', {name:/P&L/i})).toBeNull();
  });
  it('uses a slower broker refresh but preserves live WebSocket mark and exact P&L', () => {
    mockPositionsData = {net: [{exchange:'BFO',tradingsymbol:'SENSEX26CE',
      quantity:340, pnl:-10737, last_price:167.2, multiplier:1}], day:[]};
    mockLtpData = {'BFO:SENSEX26CE': {last_price:207.3}};
    const onOpen = vi.fn();
    render(<KiteFooterPnl onOpenPositions={onOpen} />);
    expect(spyPositions).toHaveBeenCalledWith(true, 5_000);
    expect(spyLtp).toHaveBeenCalledWith(['BFO:SENSEX26CE'], true, 30_000);
    const button = screen.getByRole('button',{name:/P&L/i});
    expect(button).toHaveTextContent('+₹2,897.00');
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});

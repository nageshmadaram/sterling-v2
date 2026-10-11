import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import {
  AdaptiveEdgePositionCalculator,
  getInstrumentLotSize,
} from '../AdaptiveEdgePositionCalculator';

describe('AdaptiveEdgePositionCalculator', () => {
  it('requires the dated contract lot size and refuses to guess from its name', () => {
    expect(getInstrumentLotSize('BANKNIFTY26NOV55200CE')).toBe(0);
    render(<AdaptiveEdgePositionCalculator symbol="BANKNIFTY26NOV55200CE" />);
    expect(screen.getByText('Lot size unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Place BUY/ })).toBeNull();
  });

  it('renders default entry, covered points, unrealized P&L, and defined SL risk', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        lotSize={25}
        defaultEntryPrice={500}
        defaultSl={400}
        defaultTsl={520}
        defaultExit={650}
        currentLtp={525}
        optionType="CE"
      />,
    );

    expect(screen.getByText('Position Sizing & Trade Plan')).toBeInTheDocument();
    // Default 1 Lot * 25 Qty = 25 Qty
    expect(screen.getByText('25 Qty (1 Lot × 25)')).toBeInTheDocument();
    // Capital Deployed: 500 * 25 = ₹12,500.00
    expect(screen.getByText(/₹12,500\.00/)).toBeInTheDocument();
    // Covered Points: 525 - 500 = +25.00 pts (+5.00%)
    expect(screen.getByText('+25.00 pts (+5.00%)')).toBeInTheDocument();
    // Unrealized P&L: 25 pts * 25 Qty = +₹625.00
    expect(screen.getByText('+₹625.00')).toBeInTheDocument();
    // Hard SL Risk: (500 - 400) * 25 = -₹2,500.00
    expect(screen.getByText(/-₹2,500\.00/)).toBeInTheDocument();
    // TSL Locked: (520 - 500) * 25 = +₹500.00
    expect(screen.getByText(/\+₹500\.00/)).toBeInTheDocument();
    // Target Reward: (650 - 500) * 25 = +₹3,750.00
    expect(screen.getByText(/\+₹3,750\.00/)).toBeInTheDocument();
  });

  it('updates calculations live when changing lots and editing entry price', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        lotSize={25}
        defaultEntryPrice={500}
        defaultSl={400}
        defaultTsl={500}
        defaultExit={600}
        currentLtp={550}
      />,
    );

    // Increase lots from 1 to 2
    const plusBtn = screen.getByRole('button', { name: '+' });
    fireEvent.click(plusBtn);

    // Now 2 Lots * 25 = 50 Qty
    expect(screen.getByText('50 Qty (2 Lots × 25)')).toBeInTheDocument();
    // Capital Deployed: 500 * 50 = ₹25,000.00
    expect(screen.getByText(/₹25,000\.00/)).toBeInTheDocument();
    // Unrealized P&L: (550 - 500) * 50 = +₹2,500.00
    expect(screen.getByText('+₹2,500.00')).toBeInTheDocument();

    // Reset button appears when customized
    const resetBtn = screen.getByRole('button', { name: 'Reset Defaults' });
    fireEvent.click(resetBtn);

    // Reverts back to 1 lot
    expect(screen.getByText('25 Qty (1 Lot × 25)')).toBeInTheDocument();
    expect(screen.getByText(/₹12,500\.00/)).toBeInTheDocument();
  });

  it('rounds raw floating-point stop loss and entries to nearest 0.05 tick multiple', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        lotSize={25}
        defaultEntryPrice={504.35}
        defaultSl={175.77233038426942}
        defaultTsl={473.4812}
        defaultExit={750.887}
        currentLtp={543.1}
        optionType="CE"
      />,
    );

    // 175.77233... rounded to nearest 0.05 tick is 175.75
    // SL distance: 504.35 - 175.75 = 328.60 pts
    // Risk: 328.60 * 25 = ₹8,215.00
    expect(screen.getByText(/-₹8,215\.00/)).toBeInTheDocument();
    expect(screen.getByText(/-328\.60 pts/)).toBeInTheDocument();
    expect(screen.getByText(/₹473\.50/)).toBeInTheDocument();
  });

  it('omits TSL from the plan and from copy when hideTsl', () => {
    const writes: string[] = [];
    Object.assign(navigator, {
      clipboard: { writeText: (t: string) => { writes.push(t); return Promise.resolve(); } },
    });
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY26AUG25000CE"
        tradingsymbol="NIFTY26AUG25000CE"
        defaultEntryPrice={18}
        defaultSl={14}
        defaultTsl={16}
        defaultExit={26}
        currentLtp={18}
        optionType="CE"
        hideTsl
      />,
    );
    expect(screen.queryByText('Trail (TSL)')).not.toBeInTheDocument();
    expect(screen.queryByText('TSL (₹)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Copy Plan/ }));
    expect(writes).toHaveLength(1);
    expect(writes[0]).not.toMatch(/TSL:/);
    expect(writes[0]).toMatch(/Stop Loss/);
  });

  it('treats the whole premium as at risk when hideTsl', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY26AUG25000CE"
        tradingsymbol="NIFTY26AUG25000CE"
        lotSize={75}
        defaultLots={2}
        defaultEntryPrice={18}
        defaultSl={14}
        defaultExit={26}
        currentLtp={18}
        optionType="CE"
        hideTsl
      />,
    );
    expect(screen.getByText('Premium at risk')).toBeInTheDocument();
    // 18 × 150 = ₹2,700 — the board row's maxLossInr, not stop-distance × qty.
    expect(screen.getAllByText('-₹2,700.00').length).toBeGreaterThan(0);
  });
});

it('groups the user example without duplicate movement or reward metrics', () => {
  render(<AdaptiveEdgePositionCalculator symbol="BANKNIFTY26NOV55200CE" lotSize={30} defaultEntryPrice={1373}
    defaultSl={1174.05} defaultTsl={1331.25} defaultExit={1770.90} currentLtp={1415.20} exitState="0/3 red" />);
  expect(screen.getByText('+₹1,266.00')).toBeInTheDocument();
  expect(screen.getByText(/-₹5,968.50/)).toBeInTheDocument();
  expect(screen.getByText(/-₹1,252.50/)).toBeInTheDocument();
  expect(screen.getByText(/\+₹11,937.00/)).toBeInTheDocument();
  expect(screen.queryByText('Points covered')).toBeNull();
  expect(screen.getByText('BANKNIFTY NOV 55200 CE')).toBeInTheDocument();
});
it('does not invent a target for a strategy that supplies none', () => {
  render(<AdaptiveEdgePositionCalculator symbol="NIFTY26O0925000CE" lotSize={75} defaultEntryPrice={100} defaultSl={80} />);
  expect(screen.getByText('No fixed target supplied')).toBeInTheDocument();
});

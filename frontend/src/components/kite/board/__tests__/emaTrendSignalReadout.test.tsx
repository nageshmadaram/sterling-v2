import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { SignalBoard, BOARD_COLUMNS } from '../SignalBoard';
import { emaTrendSetupsToBoard, emaTrendCampaignsToBoard } from '../emaTrendAdapter';
import { emaSetup, emaCampaign } from '../../../../test/emaTrendFixtures';

describe('EMA table row evidence', () => {
  it('shows stock/profile tags and real bar values in both row states', () => {
    const row = emaTrendSetupsToBoard([emaSetup('scalping')])[0];
    const table = (openId: string | null) => <SignalBoard signals={[row]} columns={BOARD_COLUMNS}
      rowScroll openId={openId} onToggle={() => {}} nowMs={row.atMs!} />;
    const view = render(table(null));
    const strip = screen.getByTestId('ema-signal-readout');
    expect(strip).toHaveTextContent('STOCK');
    expect(strip).toHaveTextContent('Scalp');
    expect(strip).toHaveStyle({ overflowX: 'auto', whiteSpace: 'nowrap' });
    expect(strip).toHaveTextContent('EMA10: 101.13');
    expect(strip).toHaveTextContent('Entry trigger: 102.50');
    const cells = view.container.querySelector('.sb-row')!;
    expect(cells.querySelector('[data-cell="ltp"]')).toHaveTextContent('102.00');
    expect(cells.querySelector('[data-cell="entry"]')).toHaveTextContent('102.50');
    expect(cells.querySelector('[data-cell="stop"]')).toHaveTextContent('98.00');
    view.rerender(table(row.id));
    expect(screen.getByText('Setup evidence')).toBeInTheDocument();
    expect(screen.getByTestId('ema-signal-readout')).toHaveTextContent('STOCK');
    expect(screen.getByText('Context bars')).toBeInTheDocument();
  });
  it('labels indices, futures, calls and puts using the instrument contract', () => {
    const base = emaSetup('ultra_scalping');
    const contract = { exchange: 'NFO', tradingsymbol: 'TEST', instrument_type: 'CE' as const,
      lot_size: 25, tick_size: '0.05', expiry: '2026-11-26',
      execution_vehicle: 'OPTION' as const, product: 'NRML' as const,
      capital_model: 'LONG_PREMIUM', signal_source: 'SELECTED_CONTRACT' };
    const rows = [
      emaTrendSetupsToBoard([{ ...base, symbol: 'NSE:NIFTY', watch_only: true,
        signal_source: 'INDEX_SPOT_WATCH_ONLY' }])[0],
      emaTrendSetupsToBoard([{ ...base, id: 'fut', symbol: 'NFO:TESTFUT', contract: { ...contract,
        instrument_type: 'FUT', execution_vehicle: 'FUTURE' } }])[0],
      emaTrendSetupsToBoard([{ ...base, id: 'call', symbol: 'NFO:TESTCE', contract }])[0],
      emaTrendSetupsToBoard([{ ...base, id: 'put', symbol: 'NFO:TESTPE', contract: {
        ...contract, instrument_type: 'PE' } }])[0],
    ];
    rows.forEach((row, i) => {
      const v = render(<SignalBoard signals={[row]} columns={BOARD_COLUMNS}
        rowScroll openId={null} onToggle={() => {}} nowMs={row.atMs!} />);
      const strip = within(v.container).getByTestId('ema-signal-readout');
      expect(strip).toHaveTextContent(['INDEX · WATCH ONLY', 'FUTURE', 'CALL · CE', 'PUT · PE'][i]);
      expect(strip).toHaveTextContent('Ultrascalp');
      v.unmount();
    });
    expect(rows[0].executionEligible).toBe(false);
    const unavailable = emaTrendSetupsToBoard([{ ...base, id: 'unknown',
      symbol: 'NFO:UNVERIFIED' }])[0];
    const ui = render(<SignalBoard signals={[unavailable]} columns={BOARD_COLUMNS} rowScroll
      openId={null} onToggle={() => {}} nowMs={unavailable.atMs!} />);
    expect(within(ui.container).getByTestId('ema-signal-readout')).toHaveTextContent('CONTRACT TYPE UNAVAILABLE');
    ui.unmount();
  });
  it('does not synthesize entry, stop or indicators when missing', () => {
    const row = emaTrendSetupsToBoard([{ ...emaSetup(), ceiling: null, stop: null,
      feature: { ...emaSetup().feature, ema10: null } }])[0];
    render(<SignalBoard signals={[row]} columns={BOARD_COLUMNS} rowScroll
      openId={null} onToggle={() => {}} nowMs={row.atMs!} />);
    const strip = screen.getByTestId('ema-signal-readout');
    expect(strip).toHaveTextContent('Entry trigger: —');
    expect(strip).toHaveTextContent('EMA10: —');
  });
  it('shows campaign marks and position size while collapsed', () => {
    const row = emaTrendCampaignsToBoard([emaCampaign('intraday')])[0];
    render(<SignalBoard signals={[row]} columns={BOARD_COLUMNS} rowScroll
      openId={null} onToggle={() => {}} nowMs={row.atMs!} />);
    const strip = screen.getByTestId('ema-signal-readout');
    expect(strip).toHaveTextContent('STOCK');
    expect(strip).toHaveTextContent('Intraday');
    expect(strip).toHaveTextContent('Last mark: 115.00');
    expect(strip).toHaveTextContent('Held units: 28');
  });
});

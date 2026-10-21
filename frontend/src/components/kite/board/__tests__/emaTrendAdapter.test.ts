import { describe, expect, it } from 'vitest';
import { emaCampaign, emaSetup } from '../../../../test/emaTrendFixtures';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from '../emaTrendAdapter';
import { emaTrendChartSeries } from '../../../charts/EmaTrendChart';

describe('EMA Trend equity projections', () => {
  it('preserves the equity price axis and null unknown sizing instead of fabricating an option plan', () => {
    const [row] = emaTrendSetupsToBoard([emaSetup('scalping')]);
    expect(row.instrument).toMatchObject({ symbol: 'TCS', exchange: 'NSE', kind: 'equity', lotSize: 1, quoteKey: 'NSE:TCS' });
    expect(row.levels).toEqual({ ltp: 102, entry: 102.5, stop: 98, trail: null, target: null, exit: null });
    expect(row.sizing.quantity).toBeNull();
    expect(row.sections[0].stats).toContainEqual({ label: 'Close', value: '102.00' });
    expect(row.sections[0].stats).toContainEqual({ label: 'Entry trigger', value: '102.50' });
    expect(row.sections[0].stats).toContainEqual({ label: 'Stop', value: '98.00' });
    expect(row.executionEligible).toBe(true);
    expect(row.emaTrend?.mode).toBe('scalping');
  });
  it('denies historical and preactivation setups even when their technical state is armed', () => {
    expect(emaTrendSetupsToBoard([emaSetup()], true)[0].executionEligible).toBe(false);
    expect(emaTrendSetupsToBoard([{ ...emaSetup(), activation_eligible: false }])[0].executionEligible).toBe(false);
    expect(emaTrendSetupsToBoard([{ ...emaSetup(), activation_eligible: undefined }])[0].executionEligible).toBe(false);
  });
  it('keeps an expired broker setup visible but disables its entry action', () => {
    const [row] = emaTrendSetupsToBoard([{ ...emaSetup(), entry_window_open: false, runtime_blockers: ['ENTRY_EXPIRED'] }]);
    expect(row.executionEligible).toBe(false);
    expect(row.flags?.[0].label).toBe('EXPIRED');
    expect(row.flags?.[0].hint).toContain('ENTRY_EXPIRED');
  });
  it('shows Q0=103 separately from 28 held shares; 4R stays a partial milestone, not a final target', () => {
    const [row] = emaTrendCampaignsToBoard([emaCampaign()]);
    expect(row.sizing.quantity).toBe(28);
    expect(row.flags?.[0].label).toBe('28/103 shares');
    expect(row.levels.target).toBeNull();
    expect(row.sections[0].stats).toContainEqual({ label: '4R partial level', value: '120.00' });
    expect(row.sections[0].stats).toContainEqual({ label: 'Realized net', value: '₹355.25' });
    expect(row.emaTrend?.stateRevision).toBe(4);
  });
  it('surfaces pending emergency close and durable flatten rather than treating it as completed', () => {
    const pending = { id: 'emergency', kind: 'FLATTEN', side: 'SELL' as const,
      quantity: 28, filled_quantity: 0, status: 'UNKNOWN', limit: '100', at_ms: Date.now() };
    const [row] = emaTrendCampaignsToBoard([{ ...emaCampaign(), flatten_requested: true, pending_close: pending }]);
    expect(row.flags?.[1].label).toBe('Emergency close pending');
    expect(row.emaTrend?.pending).toBe(true);
    expect(row.emaTrend?.flattenRequested).toBe(true);
    expect(row.sections[0].stats).toContainEqual({ label: 'Full close requested', value: 'Yes — verify reconciliation' });
    expect(row.status).not.toBe('ended');
  });
  it('does not hide a CLOSED campaign while an order is still unsettled', () => {
    const entry = { id: 'unknown-entry', kind: 'ENTRY', side: 'BUY' as const,
      quantity: 4, filled_quantity: 0, status: 'UNKNOWN', limit: '100', at_ms: Date.now() };
    const [unresolved] = emaTrendCampaignsToBoard([{ ...emaCampaign(), state: 'CLOSED', qty: 0,
      lots: [], pending: entry, flatten_requested: true }]);
    expect(unresolved.status).toBe('error');
    expect(unresolved.emaTrend?.pending).toBe(true);
    expect(unresolved.flags?.[1].label).toBe('Close requested');
    const [settled] = emaTrendCampaignsToBoard([{ ...emaCampaign(), state: 'CLOSED', qty: 0, lots: [], pending: null }]);
    expect(settled.status).toBe('ended');
  });
  it('plots exact server-computed values and does not fill missing warmup or context with zero', () => {
    const f = emaSetup().feature;
    const series = emaTrendChartSeries([{ ...f, ema10: null, dema50: null, context_st_line: null },
      { ...f, bar: { ...f.bar, close_ms: f.bar.close_ms + 60000 }, ema10: 101.234567, dema50: 98.654321 }]);
    expect(series.ema10).toHaveLength(1);
    expect(series.ema10[0].value).toBe(101.234567);
    expect(series.dema50[0].value).toBe(98.654321);
    expect(series.context).toHaveLength(1);
    expect(series.context[0].value).toBe(92.5);
  });
});

it('preserves option contract identity and lot quantities instead of rendering equity shares', () => {
  const c = { ...emaCampaign(), symbol: 'NFO:NIFTY26NOV25000CE', instrument_type: 'CE' as const, lot_size: 25,
    expiry: '2026-11-26', qty: 100, product: 'NRML' as const };
  const row = emaTrendCampaignsToBoard([c])[0];
  expect(row.instrument.kind).toBe('option');
  expect(row.instrument.optionType).toBe('CE');
  expect(row.instrument.lotSize).toBe(25);
  expect(row.sizing.lots).toBe(4);
  expect(row.flags?.[0].label).toContain('units');
});


it('keeps spot index setups visible but never eligible for trading', () => {
  const [row] = emaTrendSetupsToBoard([{
    ...emaSetup('intraday'), symbol: 'NSE:NIFTY BANK',
    watch_only: true, signal_source: 'INDEX_SPOT_WATCH_ONLY',
  }]);
  expect(row.instrument.quoteKey).toBe('NSE:NIFTY BANK');
  expect(row.instrument.kind).toBe('index');
  expect(row.origin?.hint).toContain('watch only');
  expect(row.flags?.[0].label).toBe('INDEX WATCH');
  expect(row.executionEligible).toBe(false);
});

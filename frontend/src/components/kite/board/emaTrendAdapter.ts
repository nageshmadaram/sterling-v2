import type { BoardSignal, BoardSection, BoardInstrument } from './boardTypes';
import { normalizeMode } from './boardTypes';
import { EMA_TREND_MODE_LABEL, type DecimalValue, type EmaTrendCampaign, type EmaTrendSetup } from '../../../types/emaTrend';
import { fmtMoney, fmtPx } from '../../../lib/format';

const number = (value: DecimalValue | null | undefined): number | null => {
  if (value == null || value === '') return null;
  const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null;
};
const px = (value: DecimalValue | null | undefined) => fmtPx(number(value));
function instrument(symbol: string, meta?: { instrument_type?: string; lot_size?: number; expiry?: string }, indexWatch = false): BoardInstrument {
  const type=meta?.instrument_type || 'EQ';
  const kind=indexWatch ? 'index' as const : type==='FUT' ? 'future' as const : type==='CE' || type==='PE' ? 'option' as const : 'equity' as const;
  const [exchange, ...parts] = symbol.split(':');
  return { symbol: parts.join(':') || symbol, exchange: parts.length ? exchange : 'NSE', kind, optionType: type==='CE' || type==='PE' ? type : undefined, expiry: meta?.expiry || null,
    quoteKey: symbol.includes(':') ? symbol : `NSE:${symbol}`, lotSize: meta?.lot_size || 1 };
}
export function emaTrendSetupsToBoard(rows: readonly EmaTrendSetup[], historical = false): BoardSignal[] {
  return rows.map(row => {
    const f = row.feature;
    const spotWatch = row.watch_only === true || row.signal_source === 'INDEX_SPOT_WATCH_ONLY';
    const sections: BoardSection[] = [{ title: 'Setup evidence', stats: [
      { label: 'Reason', value: row.reason }, { label: 'Path', value: row.path || 'Waiting' },
       { label: 'Close', value: px(f.bar.close) }, { label: 'Entry trigger', value: px(row.ceiling) }, { label: 'Stop', value: px(row.stop) },
      { label: 'EMA10', value: px(f.ema10) }, { label: 'EMA21', value: px(f.ema21) },
      { label: 'DEMA50', value: px(f.dema50) }, { label: 'ATR10', value: px(f.atr10) },
      { label: 'Supertrend (10,3)', value: `${f.st_direction} · ${px(f.st_line)}` },
      { label: 'Context ST', value: f.context_st }, { label: 'Context bars', value: f.context_count },
      { label: 'Context close', value: f.context_close_ms ? new Date(f.context_close_ms).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : 'Unavailable' },
      { label: 'Feature readiness', value: f.ready ? 'Ready' : 'Warming up' }, { label: 'Config revision', value: row.config_revision },
    ] }];
    return { id: row.id, engine: 'ema_trend', mode: normalizeMode(row.mode), underlying: row.symbol.split(':').pop()!, instrument: instrument(row.symbol,row.contract,spotWatch),
      direction: 'long', status: row.status === 'expired' ? 'ended' : row.status, atMs: row.close_ms,
      levels: { ltp: number(f.bar.close), entry: number(row.ceiling), stop: number(row.stop), trail: null, target: null, exit: null },
      sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null }, score: null, reason: row.reason,
      historical, executionEligible: !historical && !spotWatch && row.status === 'armed' && row.activation_eligible === true && row.entry_window_open !== false, planPriced: true,
      origin: { label: EMA_TREND_MODE_LABEL[row.mode], hint: spotWatch ? 'Index signal, watch only.' : 'From your selected names, finished candles only.', tone: 'blue' },
      flags: [{ label: spotWatch ? 'INDEX WATCH' : row.runtime_blockers?.includes('ENTRY_EXPIRED') ? 'EXPIRED' : historical ? 'RECORDED' : 'BROKER DATA', hint: row.runtime_blockers?.join(' · ') || 'Data source, not trade mode.', tone: row.entry_window_open === false ? 'amber' : 'blue' }, { label: f.st_direction, hint: 'Signal-timeframe Supertrend direction.', tone: f.st_direction === 'UP' ? 'green' : 'dim' }],
      sections, emaTrend: { signalId: row.id, mode: row.mode, configRevision: row.config_revision },
    };
  });
}
export function emaTrendCampaignsToBoard(campaigns: readonly EmaTrendCampaign[]): BoardSignal[] {
  return campaigns.map(c => {
    const qty = c.qty ?? c.lots.reduce((sum, lot) => sum + lot.qty, 0);
    const current = number(c.last_mark), initial = number(c.p0), r = number(c.r_price), stop = number(c.stop);
    const unsettled = Boolean(c.pending || c.pending_close);
    return { id: `campaign:${c.id}`, engine: 'ema_trend', held: true, mode: normalizeMode(c.mode), underlying: c.symbol.split(':').pop()!, instrument: instrument(c.symbol,c),
      direction: 'long', status: (c.state === 'RECOVERY_REQUIRED' || (c.state === 'CLOSED' && unsettled)) ? 'error' : c.state === 'CLOSED' ? 'ended' : c.below10 || c.below21 ? 'weakening' : 'running',
      atMs: c.last_close_ms || null, historical: c.tenant_kind === 'REPLAY', executionEligible: false,
      levels: { ltp: current, entry: initial && initial > 0 ? initial : null, stop: number(c.initial_stop), trail: stop, target: null, exit: null },
      sizing: { lots: c.instrument_type && c.instrument_type!=='EQ' ? qty/(c.lot_size || 1) : null, quantity: qty, atRiskInr: current != null && stop != null ? Math.max(0, current - stop) * qty : null,
        deployedInr: number(c.vwap) == null ? null : Number(c.vwap) * qty },
      score: null, reason: c.reason, exitProgress: `${c.below10}/2 · ${c.below21}/2`,
      origin: { label: EMA_TREND_MODE_LABEL[c.mode], hint: `${c.product} · ${c.state}`, tone: 'blue' },
      flags: [{ label: `${qty}/${c.q0} ${c.instrument_type && c.instrument_type!=='EQ' ? 'units' : 'shares'}`, hint: 'Held now / bought at start.', tone: 'green' },
        { label: c.pending_close ? 'Emergency close pending' : c.flatten_requested ? 'Close requested' : c.pending ? `${c.pending.kind} pending` : 'PAPER', hint: c.protection, tone: 'amber' }],
      sections: [{ title: 'Campaign and risk', stats: [
        { label: 'State', value: c.state }, { label: 'Contract', value: `${c.instrument_type || 'EQ'} · lot ${c.lot_size || 1} · ${c.product}${c.expiry ? ` · ${c.expiry}` : ''}` }, { label: 'Held / initial units', value: `${qty} / ${c.q0}` },
        { label: 'Original R / unit', value: px(r) }, { label: '4R partial level', value: initial && r ? px(initial + 4 * r) : '—' },
        { label: 'Current inventory VWAP', value: px(c.vwap) }, { label: 'Realized net', value: fmtMoney(number(c.realized_net)) },
        { label: 'Fees', value: fmtMoney(number(c.total_fees)) }, { label: 'Protection', value: c.protection },
        { label: 'Pending filled / requested', value: c.pending ? `${c.pending.filled_quantity} / ${c.pending.quantity}` : c.pending_close ? `${c.pending_close.filled_quantity} / ${c.pending_close.quantity} (emergency close)` : 'None' },
        { label: 'Full close requested', value: c.flatten_requested ? 'Yes — verify reconciliation' : 'No' },
        { label: '4R milestone', value: c.took_4r ? 'Consumed' : 'Not consumed' },
      ] }], emaTrend: { campaignId: c.id, stateRevision: c.revision, configRevision: c.config_revision, mode: c.mode, campaignState: c.state, pending: unsettled, flattenRequested: Boolean(c.flatten_requested) },
    };
  });
}

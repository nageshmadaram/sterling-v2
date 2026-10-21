/** Gamma-Unwind snapshot -> shared board contract. */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import { normalizeMode } from './boardTypes';
import { LABELS, fmtMoney, fmtPnl, fmtPx, fmtQty } from '../../../lib/format';
import type {
  GammaEntryGate, GammaGateContext, GammaMoveSnapshot, GammaPositionRow,
  GammaSignalRow,
} from '../../../hooks/useGammaMove';

const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;
const num = (v: number | null | undefined, dp = 2) => fmtPx(v, dp);

/** The hard stop set at entry. Only DTE<=1 has one: a swing has no premium stop before T1. */
function hardStop(pos: GammaPositionRow): number | null {
  return pos.hold === 'intraday' ? price(pos.base_low) : null;
}

/** What is actually protecting the live position right now.
 *
 * `pos.stop` is the strategy's desired/current stop. In broker/both mode that
 * number is not restart-safe protection until the GTT is confirmed at the same
 * quantity/level (`broker_stop`). Monitor-only mode deliberately has no GTT, so
 * its in-process stop remains the active value.
 */
function activeProtectionStop(pos: GammaPositionRow): number | null {
  const desired = price(pos.stop);
  if (pos.stop_mode === 'monitor') return desired;
  return pos.broker_stop ? price(pos.gtt_at) : null;
}

/** Rupees lost if the CURRENT stop is honoured (zero once the stop sits at entry). */
function riskInr(entry: number | null, stop: number | null, qty: number | null): number | null {
  if (entry == null || stop == null || qty == null || qty <= 0) return null;
  return Math.max(0, (entry - stop) * qty);
}

function unrealisedInr(pos: GammaPositionRow): number | null {
  const ltp = price(pos.ltp);
  const entry = price(pos.effective_entry);
  if (ltp == null || entry == null || pos.quantity <= 0) return null;
  return (ltp - entry) * pos.quantity + (pos.partial_realised_inr ?? 0);
}

const GATE_FLAG: Record<string, { label: string; tone: BoardOrigin['tone'] }> = {
  reference_swing_protection_unavailable: { label: 'SWING · NOT LIVE', tone: 'amber' },
  live_intraday_broker_protection_required: { label: 'STOP MODE', tone: 'amber' },
  intraday_entry_cutoff: { label: 'AFTER 15:20', tone: 'amber' },
  live_execution_disabled: { label: 'LIVE OFF', tone: 'amber' },
  broker_mode_unknown: { label: 'MODE UNKNOWN', tone: 'dim' },
  signal_stale: { label: 'EXPIRED', tone: 'dim' },
};

/** Why an ENTRY cannot be taken now, or the hold it will use when it can. */
function gateFlags(gate?: GammaEntryGate): BoardOrigin[] {
  if (!gate) return [];
  if (gate.eligible) {
    return [{
      label: gate.hold.toUpperCase(), tone: 'dim',
      hint: gate.hold === 'intraday'
        ? 'Expiry tomorrow or today: stop at base low, exit by 15:20.'
        : 'Swing: stop on a daily close past the zone, exit on day 5.',
    }];
  }
  const known = GATE_FLAG[gate.code];
  return known ? [{ label: known.label, tone: known.tone, hint: gate.reason }] : [];
}

function instrument(row: GammaSignalRow): BoardInstrument {
  const i = row.instrument;
  return {
    symbol: i.tradingsymbol || row.underlying,
    exchange: i.exchange || 'NFO',
    kind: 'option',
    optionType: i.option_type,
    strike: i.strike,
    expiry: i.expiry,
    lotSize: i.lot_size,
    moneyness: row.tag || null,
    quoteKey: i.tradingsymbol ? (i.exchange || 'NFO') + ':' + i.tradingsymbol : null,
  };
}

function tierOrigin(row: GammaSignalRow): BoardOrigin {
  if (row.tier === 'ENTRY') {
    return {
      label: 'ENTRY 4/4', tone: 'green',
      hint: 'E1 fired and ZONE, LIQUIDITY, DIRECTION and UNWIND all passed.',
    };
  }
  if (row.tier === 'NEAR ENTRY') {
    return {
      label: 'NEAR ENTRY 3/4', tone: 'amber',
      hint: 'Breakout with 3 of 4 checks. Alert only, no trade.',
    };
  }
  if (row.tier === 'STRONG_WATCHLIST') {
    return {
      label: 'STRONG 3/4', tone: 'amber',
      hint: 'E1 fired with exactly three gate passes. Alert-only; Sterling will not enter it.',
    };
  }
  if (row.tier === 'WATCH ONLY') {
    return {
      label: 'WATCH ONLY', tone: 'dim',
      hint: 'Watching, or breakout with 2 or fewer checks. No trade.',
    };
  }
  return {
    label: 'WATCHLIST', tone: 'dim',
    hint: 'Watching or E1 with two or fewer gate passes. Dashboard-only.',
  };
}

function gateMarks(g?: GammaGateContext | null): BoardOrigin[] {
  if (!g) return [];
  const out: BoardOrigin[] = [];
  for (const name of ['zone', 'liquidity', 'direction', 'unwind']) {
    const pass = g.passes.includes(name);
    const unknown = g.gate_unknown.includes(name);
    out.push({
      label: (pass ? '✓ ' : unknown ? '? ' : '✗ ') + name.toUpperCase(),
      tone: pass ? 'green' : unknown ? 'dim' : 'amber',
      hint: pass
        ? name + ' gate passed'
        : unknown
          ? name + ' gate input was unavailable and therefore did not count as a pass'
          : name + ' gate failed',
    });
  }
  if (g.quadrant) {
    out.push({
      label: g.quadrant,
      tone: g.quadrant === 'SC' ? 'green' : 'dim',
      hint: 'Premium/OI quadrant relative to the day OI-peak bar.',
    });
  }
  return out;
}

function warnings(row: GammaSignalRow): BoardOrigin[] {
  const out: BoardOrigin[] = (row.warnings ?? []).map((text) => ({
    label: text.includes('Expires') ? 'EXPIRY' : 'LATE',
    tone: 'amber',
    hint: text,
  }));
  if (row.source === 'historical_replay' || row.snapshot?.historical_replay === true) {
    out.unshift({
      label: 'HISTORICAL',
      tone: 'blue',
      hint: 'Past day, rebuilt from 1-minute candles. View only.',
    });
  }
  return out;
}

function positionMarks(pos?: GammaPositionRow): BoardOrigin[] {
  if (!pos) return [];
  const out: BoardOrigin[] = [
    {
      label: pos.hold.toUpperCase(),
      tone: pos.hold === 'intraday' ? 'blue' : 'purple',
      hint: pos.hold === 'intraday'
        ? 'DTE≤1: base-low premium SL and 15:20 exit.'
        : 'Swing: stop on a daily close past the zone. Held at most the set days.',
    },
  ];
  if (pos.trade_state === 'T1_TRAIL') {
    out.push({
      label: 'T1 HIT', tone: 'green',
      hint: 'Target 1 hit. Stop moved to entry price.',
    });
  } else if (pos.hold === 'swing') {
    out.push({
      label: pos.swing_armed ? 'D68 ARMED' : 'D68 WAIT',
      tone: pos.swing_armed ? 'amber' : 'dim',
      hint: pos.swing_armed
        ? 'Price closed past the zone once. Another close back exits the trade.'
        : 'Swing pre-T1 has no premium SL. D68 has not armed yet.',
    });
  }
  if (pos.status === 'pending') {
    out.push({
      label: 'RECONCILE', tone: 'amber',
      hint: 'Order sent; fill not confirmed yet. No second order is sent.',
    });
  }
  if (pos.broker_stop) {
    out.push({
      label: 'GTT ARMED', tone: 'green',
      hint: 'Broker stop is synchronized to the full durable quantity.',
    });
  } else if (pos.stop != null && pos.stop > 0 && pos.stop_mode !== 'monitor') {
    out.push({
      label: 'STOP UNSYNC', tone: 'amber',
      hint: 'Stop not yet confirmed at Zerodha.',
    });
  }
  if (pos.exiting) {
    out.push({
      label: 'EXITING', tone: 'amber',
      hint: 'Exit order is working at Zerodha.',
    });
  }
  if (pos.pnl_reconciliation_required) {
    out.push({
      label: 'P&L CHECK', tone: 'amber',
      hint: 'Exit not added to today\'s loss total. Check in Kite.',
    });
  }
  return out;
}

function gateSection(row: GammaSignalRow): BoardSection {
  const g = row.gates;
  if (!g) {
    return {
      title: 'Four gates', layout: 'rows',
      summary: 'No E1 tier yet; this is an active watch/E2 state.',
      stats: [],
    };
  }
  return {
    title: 'Four gates',
    layout: 'tiles',
    summary: String(g.passes.length) + '/4 pass → ' + row.tier,
    stats: [
      { label: 'Passes', value: g.passes.join(', ') || 'none' },
      { label: 'Failed', value: g.gate_reasons.join(', ') || 'none' },
      { label: 'Unknown', value: g.gate_unknown.join(', ') || 'none' },
      { label: 'Zone gap', value: g.zone_gap_pct == null ? '—' : num(g.zone_gap_pct) + '%',
        hint: 'limit ' + num(g.zone_limit_pct) + '%' },
      { label: 'Avg vol', value: g.avg_vol_lots == null ? '—' : num(g.avg_vol_lots) + ' lots' },
      { label: 'Zero-vol', value: g.zero_vol_bars == null ? '—' : String(g.zero_vol_bars) },
      { label: 'F1 VWAP', value: g.f1 == null ? '—' : (g.f1 ? 'pass' : 'fail') },
      { label: 'F2 drift', value: g.f2 == null ? '—' : (g.f2 ? 'pass' : 'fail') },
      { label: 'Unwind', value: g.unwind == null ? '—' : (g.unwind ? 'pass' : 'fail') },
      { label: 'Quadrant', value: g.quadrant ?? '—' },
    ],
  };
}

function watchSection(row: GammaSignalRow): BoardSection {
  const m = row.metrics ?? {};
  return {
    title: row.state === 'armed' ? 'E1 breakout' : 'Watch / hidden E2',
    layout: 'tiles',
    summary: row.reason,
    stats: [
      { label: 'List', value: row.list_name || '—' },
      { label: 'Contract rank', value: row.tag || 'HOI-1' },
      { label: 'OI drop', value: m.oi_drop_pct == null ? '—' : num(m.oi_drop_pct) + '%' },
      { label: 'Vol ratio', value: m.vol_ratio == null ? '—' : num(m.vol_ratio) + '×' },
      { label: 'A / B / C / H',
        value: [m.a, m.b, m.c, m.h].map((x) => x == null ? '—' : x ? '✓' : '✗').join(' ') },
      { label: 'Base low', value: num(m.base_low) },
      { label: 'Base high', value: num(m.base_high) },
      { label: 'Zone edge', value: num(row.watch.zone_edge) },
      { label: 'DTE', value: row.watch.dte == null ? '—' : String(row.watch.dte) + ' trading days' },
      { label: 'Candidate count', value: String(row.watch.candidates_n ?? 0) },
    ],
  };
}

/** Plan quantity for an ENTRY that has not been taken: configured lots x lot size. */
function plannedSizing(row: GammaSignalRow, lots: number | undefined) {
  const lotSize = row.instrument.lot_size;
  if (row.tier !== 'ENTRY' || !lots || lots <= 0 || !lotSize || lotSize <= 0) {
    return { lots: null, quantity: null as number | null };
  }
  return { lots, quantity: lots * lotSize };
}

function planSection(
  row: GammaSignalRow, pos: GammaPositionRow | undefined, qty: number | null,
): BoardSection {
  const swing = (row.watch.dte ?? 99) > 1;
  const entry = price(pos?.effective_entry ?? row.levels.entry);
  const stop = pos ? hardStop(pos) : price(row.levels.stop);
  const live = pos ? activeProtectionStop(pos) : stop;
  const pnl = pos ? unrealisedInr(pos) : null;
  return {
    title: 'Reference trade plan',
    layout: 'tiles',
    summary: pos
      ? pos.hold + ' · ' + pos.trade_state
      : swing
        ? 'Swing: D68 daily-close stop before T1'
        : 'Intraday: base-low premium SL, exit 15:20',
    stats: [
      { label: LABELS.entry, value: num(entry) },
      { label: LABELS.sl, value: num(stop),
        hint: swing && !pos ? 'Swing has no premium SL before T1' : 'Base-low premium stop (DTE≤1 only)' },
      { label: LABELS.tsl, value: num(pos?.trade_state === 'T1_TRAIL' ? live : null),
        hint: pos && pos.stop_mode !== 'monitor'
          ? 'Trailing stop confirmed at Zerodha.'
          : 'After T1 the active stop moves to the entry price' },
      ...(pos && pos.stop_mode !== 'monitor' ? [
        { label: 'Desired stop', value: num(price(pos.stop)),
          hint: 'Stop sent to Zerodha, not confirmed yet.' },
        { label: 'Broker stop', value: pos.broker_stop ? num(price(pos.gtt_at)) : '—',
          hint: pos.broker_stop ? `Confirmed GTT #${pos.gtt_id} · Qty ${pos.gtt_qty}` : 'No fully synchronized broker GTT is confirmed.' },
      ] : []),
      { label: 'T1 +20%', value: num(pos?.t1 ?? row.levels.t1) },
      { label: LABELS.target + ' (T2 +40%)', value: num(pos?.t2 ?? row.levels.t2) },
      { label: LABELS.qty, value: fmtQty(qty) },
      { label: 'Risk', value: fmtMoney(riskInr(entry, live, qty)),
        hint: 'Rupees lost if the current stop is honoured' },
      { label: 'Capital', value: fmtMoney(entry != null && qty != null ? entry * qty : null),
        hint: 'Premium outlay: entry x quantity' },
      ...(pos ? [{ label: LABELS.pnl, value: fmtPnl(pnl),
        hint: 'Unrealised at LTP, plus cash from partial exits' }] : []),
      { label: 'Base low', value: num(pos?.base_low ?? row.metrics?.base_low) },
      { label: 'Zone edge', value: num(pos?.zone_edge ?? row.watch.zone_edge) },
    ],
  };
}

function toSignal(
  row: GammaSignalRow,
  pos: GammaPositionRow | undefined,
  historical: boolean,
  executionLots: number | undefined,
): BoardSignal {
  historical = historical || row.source === 'historical_replay' || row.snapshot?.historical_replay === true;
  const gate = historical ? undefined : row.entry_gate;
  const expired = !pos && gate?.code === 'signal_stale';
  const status: BoardStatus = pos
    ? 'running'
    : expired ? 'ended' : row.tier === 'ENTRY' ? 'armed' : 'watching';
  const planned = pos ? null : plannedSizing(row, executionLots);
  const qty = pos?.quantity ?? planned?.quantity ?? null;
  const entry = price(pos?.effective_entry ?? row.levels.entry);
  const stop = pos ? hardStop(pos) : price(row.levels.stop);
  const live = pos ? activeProtectionStop(pos) : stop;
  return {
    id: row.id,
    engine: 'gamma_move',
    mode: normalizeMode(pos?.hold ?? (row as { hold?: string }).hold ?? (row.watch?.dte != null && row.watch.dte <= 1 ? 'intraday' : 'swing')),
    historical,
    underlying: row.underlying,
    instrument: instrument(row),
    direction: row.instrument.option_type === 'PE' ? 'short' : 'long',
    status,
    atMs: row.at_ms || null,
    levels: {
      ltp: price(pos?.ltp ?? row.levels.ltp),
      entry,
      stop,
      trail: pos?.trade_state === 'T1_TRAIL' ? activeProtectionStop(pos) : null,
      target: price(pos?.t2 ?? row.levels.t2),
      exit: null,
    },
    quoteAgeS: (pos ? pos.ltp_age_s : row.levels.ltp_age_s) ?? null,
    sizing: {
      lots: pos?.lots ?? planned?.lots ?? null,
      quantity: qty,
      atRiskInr: riskInr(entry, live, qty),
      deployedInr: entry != null && qty != null ? entry * qty : null,
    },
    score: null,
    reason: gate && !gate.eligible && gate.reason ? gate.reason : row.reason,
    origin: tierOrigin(row),
    flags: [
      ...gateMarks(row.gates), ...gateFlags(pos ? undefined : gate),
      ...warnings(row), ...positionMarks(pos),
    ],
    underlyingPrice: price(row.watch.ref_price),
    planPriced: true,
    noTrailingStop: true,
    executionEligible: !historical && row.tier === 'ENTRY' && gate?.eligible !== false,
    sections: [watchSection(row), gateSection(row), planSection(row, pos, qty)],
  };
}

export function gammaMoveToBoard(snapshot?: GammaMoveSnapshot | null): BoardSignal[] {
  if (!snapshot) return [];
  const positions = new Map((snapshot.positions ?? []).map((p) => [p.signal_id, p]));
  const rows = snapshot.candidates ?? [];
  const out: BoardSignal[] = [];
  const seen = new Set<string>();
  const lots = snapshot.config?.execution_lots;

  for (const row of rows) {
    const pos = positions.get(row.id);
    seen.add(row.id);
    out.push(toSignal(row, pos, snapshot.historical === true, lots));
  }

  for (const pos of snapshot.positions ?? []) {
    if (seen.has(pos.signal_id)) continue;
    const entry = price(pos.effective_entry);
    const live = activeProtectionStop(pos);
    out.push({
      id: pos.signal_id,
      engine: 'gamma_move',
      mode: normalizeMode(pos.hold),
      historical: snapshot.historical === true,
      underlying: pos.underlying || pos.symbol,
      instrument: {
        symbol: pos.symbol, exchange: 'NFO', kind: 'option',
        quoteKey: 'NFO:' + pos.symbol,
      },
      direction: pos.symbol.endsWith('PE') ? 'short' : 'long',
      status: 'running',
      atMs: pos.entered_ms || null,
      levels: {
        ltp: price(pos.ltp), entry, stop: hardStop(pos),
        trail: pos.trade_state === 'T1_TRAIL' ? activeProtectionStop(pos) : null,
        target: price(pos.t2), exit: null,
      },
      quoteAgeS: pos.ltp_age_s ?? null,
      sizing: {
        lots: pos.lots, quantity: pos.quantity,
        atRiskInr: riskInr(entry, live, pos.quantity),
        deployedInr: entry != null ? entry * pos.quantity : null,
      },
      score: null,
      reason: pos.hold + ' · ' + pos.trade_state + ' · ' + pos.sessions_held + ' session(s)',
      origin: { label: 'HELD', tone: 'blue', hint: 'Open position, kept even if it left today\'s watchlist.' },
      flags: positionMarks(pos),
      planPriced: true,
      noTrailingStop: true,
      executionEligible: false,
      sections: [{
        title: 'Reference trade plan', layout: 'tiles',
        stats: [
          { label: LABELS.entry, value: num(entry) },
          { label: LABELS.sl, value: num(hardStop(pos)),
            hint: pos.hold === 'swing' ? 'Swing has no premium SL before T1' : 'Base-low premium stop (DTE≤1 only)' },
          { label: LABELS.tsl, value: num(pos.trade_state === 'T1_TRAIL' ? activeProtectionStop(pos) : null),
            hint: pos.stop_mode !== 'monitor'
              ? 'Trailing stop confirmed at Zerodha.'
              : 'In-process monitor stop.' },
          ...(pos.stop_mode !== 'monitor' ? [
            { label: 'Desired stop', value: num(price(pos.stop)),
              hint: 'Strategy stop requested from the broker. It is not active protection until the GTT is synchronized.' },
            { label: 'Broker stop', value: pos.broker_stop ? num(price(pos.gtt_at)) : '—',
              hint: pos.broker_stop ? `Confirmed GTT #${pos.gtt_id} · Qty ${pos.gtt_qty}` : 'No fully synchronized broker GTT is confirmed.' },
          ] : []),
          { label: 'T1 +20%', value: num(pos.t1) },
          { label: LABELS.target + ' (T2 +40%)', value: num(pos.t2) },
          { label: LABELS.qty, value: fmtQty(pos.quantity) },
          { label: LABELS.risk, value: fmtMoney(riskInr(entry, live, pos.quantity)) },
          { label: LABELS.capital, value: fmtMoney(entry != null ? entry * pos.quantity : null) },
          { label: LABELS.pnl, value: fmtPnl(unrealisedInr(pos)) },
          { label: 'Base low', value: num(pos.base_low) },
          { label: 'Zone edge', value: num(pos.zone_edge) },
        ],
      }],
    });
  }
  return out;
}

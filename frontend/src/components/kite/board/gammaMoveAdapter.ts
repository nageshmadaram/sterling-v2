/** Gamma-Unwind snapshot -> shared board contract. */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import type {
  GammaGateContext, GammaMoveSnapshot, GammaPositionRow, GammaSignalRow,
} from '../../../hooks/useGammaMove';

const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;
const num = (v: number | null | undefined, dp = 2) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

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
  if (row.tier === 'STRONG_WATCHLIST') {
    return {
      label: 'STRONG 3/4', tone: 'amber',
      hint: 'E1 fired with exactly three gate passes. Alert-only; Sterling will not enter it.',
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
  return (row.warnings ?? []).map((text) => ({
    label: text.includes('Expires') ? 'EXPIRY' : 'LATE',
    tone: 'amber' as const,
    hint: text,
  }));
}

function positionMarks(pos?: GammaPositionRow): BoardOrigin[] {
  if (!pos) return [];
  const out: BoardOrigin[] = [
    {
      label: pos.hold.toUpperCase(),
      tone: pos.hold === 'intraday' ? 'blue' : 'purple',
      hint: pos.hold === 'intraday'
        ? 'DTE≤1: base-low premium stop and 15:20 exit.'
        : 'Swing: D68 daily-close zone stop before T1; maximum hold is reference trading-day horizon.',
    },
  ];
  if (pos.trade_state === 'T1_TRAIL') {
    out.push({
      label: 'T1 HIT', tone: 'green',
      hint: 'Reference T1 was reached. Stop is now the actual entry price; no partial quantity was invented.',
    });
  } else if (pos.hold === 'swing') {
    out.push({
      label: pos.swing_armed ? 'D68 ARMED' : 'D68 WAIT',
      tone: pos.swing_armed ? 'amber' : 'dim',
      hint: pos.swing_armed
        ? 'Underlying has closed through the zone edge once; a close back across the edge stops the swing.'
        : 'Swing pre-T1 has no premium stop. D68 has not armed yet.',
    });
  }
  if (pos.status === 'pending') {
    out.push({
      label: 'RECONCILE', tone: 'amber',
      hint: 'Broker entry identity exists but final fill state is unresolved; no blind retry is sent.',
    });
  }
  if (pos.broker_stop) {
    out.push({
      label: 'GTT ARMED', tone: 'green',
      hint: 'Broker premium stop is synchronized to the full durable quantity.',
    });
  } else if (pos.stop != null && pos.stop > 0 && pos.stop_mode !== 'monitor') {
    out.push({
      label: 'STOP UNSYNC', tone: 'amber',
      hint: 'A reference premium stop exists but broker-side protection is not fully synchronized.',
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

function planSection(row: GammaSignalRow, pos?: GammaPositionRow): BoardSection {
  const swing = (row.watch.dte ?? 99) > 1;
  return {
    title: 'Reference trade plan',
    layout: 'tiles',
    summary: pos
      ? pos.hold + ' · ' + pos.trade_state
      : swing
        ? 'Swing: D68 daily-close stop before T1'
        : 'Intraday: base-low premium stop, exit 15:20',
    stats: [
      { label: 'Entry', value: num(pos?.effective_entry ?? row.levels.entry) },
      { label: 'Base low', value: num(pos?.base_low ?? row.metrics?.base_low) },
      { label: 'Active stop', value: num(pos?.stop ?? row.levels.stop) },
      { label: 'T1 +20%', value: num(pos?.t1 ?? row.levels.t1) },
      { label: 'T2 +40%', value: num(pos?.t2 ?? row.levels.t2) },
      { label: 'Zone edge', value: num(pos?.zone_edge ?? row.watch.zone_edge) },
    ],
  };
}

function toSignal(row: GammaSignalRow, pos?: GammaPositionRow): BoardSignal {
  const running = Boolean(pos);
  const status: BoardStatus = running ? 'running' : row.tier === 'ENTRY' ? 'armed' : 'watching';
  return {
    id: row.id,
    engine: 'gamma_move',
    underlying: row.underlying,
    instrument: instrument(row),
    direction: 'long',
    status,
    atMs: row.at_ms || null,
    levels: {
      ltp: price(row.levels.ltp),
      entry: price(pos?.effective_entry ?? row.levels.entry),
      stop: price(pos?.stop ?? row.levels.stop),
      trail: price(pos?.trade_state === 'T1_TRAIL' ? pos.stop : null),
      target: price(pos?.t2 ?? row.levels.t2),
      exit: null,
    },
    sizing: {
      lots: pos?.lots ?? null,
      quantity: pos?.quantity ?? null,
      atRiskInr: null,
      deployedInr: null,
    },
    score: null,
    reason: row.reason,
    origin: tierOrigin(row),
    flags: [...gateMarks(row.gates), ...warnings(row), ...positionMarks(pos)],
    underlyingPrice: price(row.watch.ref_price),
    planPriced: true,
    noTrailingStop: true,
    executionEligible: row.tier === 'ENTRY',
    sections: [watchSection(row), gateSection(row), planSection(row, pos)],
  };
}

export function gammaMoveToBoard(snapshot?: GammaMoveSnapshot | null): BoardSignal[] {
  if (!snapshot) return [];
  const positions = new Map((snapshot.positions ?? []).map((p) => [p.signal_id, p]));
  const rows = snapshot.candidates ?? [];
  const out: BoardSignal[] = [];
  const seen = new Set<string>();

  for (const row of rows) {
    const pos = positions.get(row.id);
    seen.add(row.id);
    out.push(toSignal(row, pos));
  }

  for (const pos of snapshot.positions ?? []) {
    if (seen.has(pos.signal_id)) continue;
    out.push({
      id: pos.signal_id,
      engine: 'gamma_move',
      underlying: pos.underlying || pos.symbol,
      instrument: {
        symbol: pos.symbol, exchange: 'NFO', kind: 'option',
        quoteKey: 'NFO:' + pos.symbol,
      },
      direction: 'long',
      status: 'running',
      atMs: pos.entered_ms || null,
      levels: {
        ltp: null, entry: price(pos.effective_entry), stop: price(pos.stop),
        trail: price(pos.trade_state === 'T1_TRAIL' ? pos.stop : null),
        target: price(pos.t2), exit: null,
      },
      sizing: { lots: pos.lots, quantity: pos.quantity, atRiskInr: null, deployedInr: null },
      score: null,
      reason: pos.hold + ' · ' + pos.trade_state + ' · ' + pos.sessions_held + ' session(s)',
      origin: { label: 'HELD', tone: 'blue', hint: 'Durable Gamma-Unwind position restored independently of the current watchlist.' },
      flags: positionMarks(pos),
      planPriced: true,
      noTrailingStop: true,
      executionEligible: false,
      sections: [{
        title: 'Reference trade plan', layout: 'tiles',
        stats: [
          { label: 'Entry', value: num(pos.effective_entry) },
          { label: 'Base low', value: num(pos.base_low) },
          { label: 'Active stop', value: num(pos.stop) },
          { label: 'T1 +20%', value: num(pos.t1) },
          { label: 'T2 +40%', value: num(pos.t2) },
          { label: 'Zone edge', value: num(pos.zone_edge) },
        ],
      }],
    });
  }
  return out;
}

import { useSimNowMs } from '../../../hooks/useReplayStore';
/**
 * Gamma-Unwind V1 board.
 *
 * The supplied client is signal/alert-only. Sterling preserves that signal
 * tiering exactly and adds a separate guarded execution adapter: ENTRY may be
 * armed, STRONG WATCHLIST alerts only, WATCHLIST is dashboard-only.
 *
 * The board polls while mounted because E2/E1 is server/ticker driven; waiting
 * until a position is already open would hide the very live ENTRY signal the
 * operator needs to see.
 */
import React from 'react';
import {
  useArmGammaMove, useBackfillGammaMoveHistory, useGammaMoveHistory,
  useGammaMoveScan, useGammaMoveSnapshot, useLoadGammaMoveHistoricalDay,
} from '../../../hooks/useGammaMove';
import type { GammaScanState, GammaTradeRecord } from '../../../hooks/useGammaMove';
import { gammaMoveToBoard } from './gammaMoveAdapter';
import { BOARD_COLUMNS, SignalBoard } from './SignalBoard';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';
import { LABELS, fmtPnl, pnlColor } from '../../../lib/format';
import { useSignalColumns } from './useSignalColumns';
import { formatInstrument } from '../../../lib/instrument';

const note: React.CSSProperties = {
  padding: '10px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6,
};

/** What the scan cost, per stage. The funnel is the reason this engine can run
 *  at all, so its shape is worth being able to see degrade. */
function ScanCost({ scan }: { scan?: GammaScanState }) {
  if (!scan?.stage_a) return null;
  const a = scan.stage_a, b = scan.stage_b, c = scan.stage_c;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 16px', padding: '6px 12px',
      fontSize: 11, color: k.dim, borderBottom: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      {scan.issue && (
        // "0 scanned" with no reason reads as a quiet market; this was a scan
        // that could not look.
        <span role="alert" data-testid="gamma-scan-issue"
              style={{ flexBasis: '100%', color: k.amber, fontWeight: 600 }}>
          Scan could not read the market — {scan.issue}
        </span>
      )}
      <span>Scanned <b style={{ color: k.text }}>{a.scanned}</b> names</span>
      <span>→ <b style={{ color: k.text }}>{a.near_level}</b> at a level</span>
      <span>→ <b style={{ color: k.text }}>{b?.candidates ?? 0}</b> strikes</span>
      <span>→ <b style={{ color: k.text }}>{c?.armed ?? 0}</b> armed of {c?.watched ?? 0}</span>
      <span style={{ marginLeft: 'auto' }}>
        {c?.historical_requests ?? 0} history calls · {scan.total_seconds ?? 0}s
      </span>
    </div>
  );
}

/** The realised record. The break-even threshold travels with the win rate,
 *  because a win rate on its own is not an answer. */
function Record({ record }: { record?: GammaTradeRecord }) {
  if (!record || !record.trades) {
    return <p style={note}>No closed trades yet.</p>;
  }
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 18px', padding: '8px 12px',
      fontSize: 11, color: k.dim, borderTop: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Trades <b style={{ color: k.text }}>{record.trades}</b></span>
      <span>Win rate <b style={{ color: k.text }}>
        {record.win_rate == null ? '—' : `${record.win_rate.toFixed(1)}%`}</b></span>
      <span>{LABELS.pnl} <b style={{ color: pnlColor(record.realised_inr) }}>{fmtPnl(record.realised_inr)}</b></span>
      {record.consecutive_losses > 0 && (
        <span style={{ color: k.amber }}>
          {record.consecutive_losses} consecutive loss{record.consecutive_losses === 1 ? '' : 'es'}
        </span>
      )}
    </div>
  );
}

function GammaSessionTimeline({
  days,
  selectedDay,
  today,
  onSelect,
}: {
  days: Array<{
    day: string;
    signals: number;
    watches: number;
    entry?: number;
    strong_watchlist?: number;
    source?: string;
    issue?: string;
  }>;
  selectedDay: string;
  today?: string;
  onSelect: (day: string) => void;
}) {
  const recent = days.filter((row) => row.day !== today).slice(0, 12);
  if (!recent.length) return null;
  return (
    <div style={{
      display: 'flex', gap: 6, overflowX: 'auto', padding: '6px 10px',
      borderBottom: `1px solid ${k.border}`,
    }}>
      {recent.map((row) => {
        const active = selectedDay === row.day;
        const source = row.source === 'live_recorded'
          ? 'live'
          : row.source === 'historical_replay' ? 'historical' : 'loading history';
        return (
          <button
            key={row.day}
            type="button"
            onClick={() => onSelect(row.day)}
            title={row.issue || `${row.signals} E1 · ${row.watches} watches · ${source}`}
            style={{
              flex: '0 0 auto', minWidth: 118, textAlign: 'left',
              padding: '5px 7px', borderRadius: 5, cursor: 'pointer',
              border: `1px solid ${active ? k.blue : k.border}`,
              background: active ? 'color-mix(in srgb, var(--k-blue, #58a6ff) 10%, transparent)' : 'transparent',
              color: k.text,
            }}
          >
            <div style={{ fontSize: 10.5, fontWeight: 700 }}>{row.day}</div>
            <div style={{ fontSize: 9.5, color: k.dim, marginTop: 2 }}>
              {row.signals} E1 · {row.watches} watch
              {row.entry != null ? ` · ${row.entry} ENTRY` : ''}
              {row.strong_watchlist != null ? ` · ${row.strong_watchlist} STRONG` : ''}
            </div>
            <div style={{ fontSize: 9, color: row.issue ? k.amber : k.dim, marginTop: 1 }}>
              {row.issue ? 'coverage issue' : source}
            </div>
          </button>
        );
      })}
    </div>
  );
}


function GammaHistoryLedger({
  rows,
  loading,
  error,
  onSelect,
}: {
  rows: Array<{
    id: string;
    at_ms: number;
    history_day?: string;
    tier: string;
    underlying: string;
    source?: string;
    history_complete?: boolean;
    instrument: { tradingsymbol: string; option_type: string; strike: number };
    list_name: string;
  }>;
  loading: boolean;
  error: unknown;
  onSelect: (day: string, signalId: string) => void;
}) {
  const [open, setOpen] = React.useState(true);
  const recent = rows.slice(0, 40);

  return (
    <div style={{ borderBottom: `1px solid ${k.border}` }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: '100%', border: 0, background: 'transparent', color: k.text,
          display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px',
          cursor: 'pointer', fontSize: 11, textAlign: 'left',
        }}
      >
        <strong>Recent E1 signal ledger</strong>
        <span style={{ color: k.dim }}>
          {loading ? 'loading…' : `${rows.length} persisted E1 event${rows.length === 1 ? '' : 's'}`}
        </span>
        <span style={{ marginLeft: 'auto', color: k.dim }}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div style={{ maxHeight: 260, overflowY: 'auto', borderTop: `1px solid ${k.border}` }}>
          {error ? (
            <p style={{ ...note, color: k.red }}>
              Could not load Gamma history: {(error as Error).message}
            </p>
          ) : recent.length === 0 && !loading ? (
            <p style={note}>
              No persisted E1 events yet. Watch rows can still exist without an E1 breakout.
              Select any past trading day; Sterling loads available historical signals automatically.
            </p>
          ) : (
            <table className="gamma-history-cards" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
              <thead>
                <tr style={{ color: k.dim }}>
                  <th style={{ textAlign: 'left', padding: '5px 12px' }}>When</th>
                  <th style={{ textAlign: 'left', padding: '5px 8px' }}>Underlying</th>
                  <th style={{ textAlign: 'left', padding: '5px 8px' }}>Contract</th>
                  <th style={{ textAlign: 'left', padding: '5px 8px' }}>Tier</th>
                  <th style={{ textAlign: 'left', padding: '5px 8px' }}>Source</th>
                  <th style={{ padding: '5px 12px' }} />
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => {
                  const day = row.history_day || new Date(row.at_ms).toLocaleDateString('en-CA', {
                    timeZone: 'Asia/Kolkata',
                  });
                  const stamp = new Date(row.at_ms).toLocaleString('en-IN', {
                    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                    hour12: false, timeZone: 'Asia/Kolkata',
                  });
                  const tone = row.tier === 'ENTRY'
                    ? k.green
                    : row.tier === 'STRONG_WATCHLIST' ? k.amber : k.dim;
                  return (
                    <tr key={day + ':' + row.id} style={{ borderTop: `1px solid ${k.border}` }}>
                      <td data-mobile-label="When" style={{ padding: '5px 12px', color: k.dim, whiteSpace: 'nowrap' }}>{stamp}</td>
                      <td data-mobile-label="Underlying" style={{ padding: '5px 8px', color: k.text }}>{row.underlying}</td>
                      <td data-mobile-label="Contract" style={{ padding: '5px 8px', color: k.text }}>
                        {row.instrument.tradingsymbol ? formatInstrument(row.instrument.tradingsymbol) : `${row.instrument.option_type} ${row.instrument.strike}`}
                      </td>
                      <td data-mobile-label="Tier" style={{ padding: '5px 8px', color: tone, fontWeight: 700 }}>{row.tier}</td>
                      <td data-mobile-label="Source" style={{ padding: '5px 8px', color: k.dim }}>
                        {row.source === 'historical_replay'
                          ? row.history_complete === false ? 'historical · partial' : 'historical'
                          : 'live'}
                      </td>
                      <td style={{ padding: '5px 12px', textAlign: 'right' }}>
                        <button
                          type="button"
                          onClick={() => onSelect(day, row.id)}
                          style={{
                            border: `1px solid ${k.border}`, background: 'transparent',
                            color: k.blue, borderRadius: 4, padding: '2px 7px',
                            fontSize: 10, cursor: 'pointer',
                          }}
                        >
                          Open day
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}


export function GammaMoveBoard({ nowMs, onOpenDetail, onOpenChart }: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  // Buy/Sell and the chart, built from the signal alone — same on every board.
  const replay = useSimNowMs() !== null;
  const rowActions = useBoardRowActions({ onOpenChart });
  const commonColumns = useSignalColumns();
  const [selectedDay, setSelectedDay] = React.useState('');
  const snapshot = useGammaMoveSnapshot(
    true,
    selectedDay ? 0 : 3000,
    replay ? undefined : selectedDay || undefined,
  );
  const scan = useGammaMoveScan();
  const historicalLoad = useLoadGammaMoveHistoricalDay();
  const backfill = useBackfillGammaMoveHistory();
  const arm = useArmGammaMove();
  const history = useGammaMoveHistory(!replay, 60, 500);

  const data = snapshot.data;
  const bootstrapDayRef = React.useRef<string | null>(null);
  const loadedHistoricalDayRef = React.useRef<string | null>(null);
  const autoBackfillRef = React.useRef<string>('');

  // On weekends/holidays, an empty "Today" view is not useful. Move the board
  // to the latest reference trading session, where a cached/live-recorded view
  // can be shown or a read-only reconstruction can be requested.
  React.useEffect(() => {
    if (
      replay
      || selectedDay
      || !data
      || data.historical
      || data.is_trading_day !== false
      || !data.latest_trading_day
      || data.latest_trading_day === data.today
    ) return;
    setSelectedDay(data.latest_trading_day);
    setOpenId(null);
  }, [
    data?.today,
    data?.historical,
    data?.is_trading_day,
    data?.latest_trading_day,
    selectedDay, replay,
  ]);

  // An arbitrary trading date can now be reconstructed from broker history.
  // Trigger it once when the selected day has no cached runtime/history row.
  React.useEffect(() => {
    if (
      replay
      || !selectedDay
      || !data?.historical
      || data.recorded
      || data.is_trading_day === false
      || !data.replay_available
      || historicalLoad.isPending
      || loadedHistoricalDayRef.current === selectedDay
    ) return;
    loadedHistoricalDayRef.current = selectedDay;
    historicalLoad.mutate({ day: selectedDay });
  }, [
    selectedDay,
    data?.historical,
    data?.is_trading_day,
    data?.recorded,
    data?.is_trading_day,
    data?.replay_available,
    historicalLoad.isPending,
  ]);

  // Populate recent history automatically. A newly installed Gamma-Unwind
  // engine has no live-recorded ledger for prior sessions, and requiring the
  // operator to discover/load each date manually makes a correct strategy
  // look like it never produced signals. Rebuild at most five recent sessions;
  // historical rows remain read-only and can never execute.
  React.useEffect(() => {
    if (replay || !data?.today || backfill.isPending) return;
    const missing = (data.history_days ?? [])
      .filter((row) =>
        row.day !== data.today
        && row.day !== selectedDay
        && row.source === 'available_replay'
      )
      .slice(0, 5)
      .map((row) => row.day);
    if (!missing.length) return;
    // One bounded automatic batch per board/day. After it completes the
    // operator can explicitly load more sessions; do not cascade through the
    // full month and exhaust broker history quota just because query invalidation
    // exposes the next five missing dates.
    if (autoBackfillRef.current === data.today) return;
    autoBackfillRef.current = data.today;
    backfill.mutate(missing);
  }, [
    data?.today,
    data?.history_days,
    selectedDay,
    backfill.isPending, replay,
  ]);

  // A newly deployed/restarted Gamma runtime may have no in-memory watchlist and
  // no pre-ledger archive yet, especially when the operator opens the board
  // outside the 09:20/13:15 scheduled build slots. Perform one explicit
  // board-driven rebuild for today's view only. A completed zero-result scan is
  // recorded, so this does not spin forever on a legitimately quiet day.
  React.useEffect(() => {
    if (
      selectedDay
      || !data
      || data.historical
      || data.is_trading_day === false
      || !data.config?.enabled
      || data.recorded
      || (data.candidates?.length ?? 0) > 0
      || scan.isPending
      || bootstrapDayRef.current === data.today
    ) return;
    bootstrapDayRef.current = data.today;
    scan.mutate();
  }, [
    data?.today,
    data?.historical,
    data?.recorded,
    data?.config?.enabled,
    data?.candidates?.length,
    selectedDay,
    scan.isPending,
  ]);

  const signals = React.useMemo(() => gammaMoveToBoard(data), [data]);
  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'gamma_move', nowMs });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading Gamma Unwind…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const blockers = data?.blockers ?? [];
  const armedRows = signals.filter((s) => s.status === 'armed');
  const rawRows = data?.candidates ?? [];
  const watchCount = rawRows.filter((row) => row.gates == null).length;
  const e1Count = rawRows.filter((row) => row.gates != null).length;
  const entryCount = rawRows.filter((row) => row.tier === 'ENTRY').length;
  const strongCount = rawRows.filter((row) => row.tier === 'STRONG_WATCHLIST').length;
  const liveEntryBlocked = (
    data?.mode?.is_paper === false
    && data?.mode?.live_execution_enabled === false
  );
  const gammaEntryBlockReason = (signalId: string): string | null => {
    if (data?.historical) return 'Historical signals are read-only.';
    if (data?.mode?.is_paper == null) return 'Broker mode is unknown.';
    const raw = (data?.candidates ?? []).find((row) => row.id === signalId);
    if (!raw || raw.tier !== 'ENTRY') return 'Only ENTRY tier may execute.';
    // The server owns the verdict (same rules arm() enforces); the local checks
    // below only cover payloads that predate it.
    if (raw.entry_gate && !raw.entry_gate.eligible) {
      return raw.entry_gate.reason || 'Entry is not available for this signal.';
    }
    if (data.mode.is_paper === false) {
      if (data.mode.live_execution_enabled === false) {
        return 'New entries are halted by the operator master switch (STERLING_LIVE_EXECUTION).';
      }
      const dte = raw.watch?.dte;
      if (dte == null || dte > 1) {
        return 'Live swing entry is blocked: Gamma-Unwind has no broker-resting premium stop before T1.';
      }
      const istClock = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(nowMs ?? Date.now()));
      const [hour, minute] = istClock.split(':').map(Number);
      if ((hour * 60 + minute) >= (15 * 60 + 20)) {
        return 'Intraday entry cutoff reached (15:20 IST).';
      }
      if (data.config?.stop_mode === 'monitor') {
        return 'Live intraday entry requires broker or both stop mode.';
      }
    }
    return null;
  };
  const armableCount = armedRows.filter((row) => gammaEntryBlockReason(row.id) == null).length;

  return (
    <div className="gamma-signal-board">
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button type="button" className="sb-help-link" onClick={() => window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: 'help' }))}>Help</button>
        {!selectedDay && (
          <button
            type="button"
            onClick={() => { if (!replay) scan.mutate(); }}
            disabled={replay || scan.isPending || data?.is_trading_day === false}
            title="Rebuild daily zones, client watchlists and the 5-minute E2 runtime now"
            style={{
              background: 'transparent', border: `1px solid ${k.border}`, color: k.text,
              borderRadius: 6, padding: '4px 10px', fontSize: 11,
              cursor: (scan.isPending || data?.is_trading_day === false) ? 'not-allowed' : 'pointer',
            }}
          >
            {scan.isPending ? 'Scanning…' : 'Scan now'}
          </button>
        )}
        {selectedDay && (historicalLoad.isPending || backfill.isPending) && (
          <span style={{ fontSize: 11, color: k.blue }}>
            Loading historical signals…
          </span>
        )}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: k.dim }}>
          Trading day
          <select
            aria-label="Gamma trading day"
            value={selectedDay}
            onChange={(e) => {
              setSelectedDay(e.target.value);
              setOpenId(null);
            }}
            style={{
              background: k.bg, color: k.text, border: `1px solid ${k.border}`,
              borderRadius: 6, padding: '4px 7px', fontSize: 11,
            }}
          >
            <option value="">Today · live</option>
            {selectedDay
              && selectedDay !== data?.today
              && !(data?.history_days ?? []).some((row) => row.day === selectedDay)
              && <option value={selectedDay}>{selectedDay} · unrecorded</option>}
            {(data?.history_days ?? [])
              .filter((row) => row.day !== data?.today)
              .map((row) => (
                <option key={row.day} value={row.day}>
                  {row.day} · {row.signals} E1 · {row.watches} watches
                </option>
              ))}
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: k.dim }}>
          Date
          <input
            aria-label="Gamma history date"
            type="date"
            max={data?.today}
            value={selectedDay || data?.today || ''}
            onChange={(e) => {
              const next = e.target.value;
              setSelectedDay(next && next !== data?.today ? next : '');
              setOpenId(null);
            }}
            style={{
              background: k.bg, color: k.text, border: `1px solid ${k.border}`,
              borderRadius: 6, padding: '3px 7px', fontSize: 11,
            }}
          />
        </label>
        <span style={{ fontSize: 11, color: k.dim }}>
          <strong style={{
            color: data?.mode?.is_paper === false
              ? k.green
              : data?.mode?.is_paper === true ? k.amber : k.dim,
          }}>
            {data?.mode?.is_paper === false
              ? 'LIVE'
              : data?.mode?.is_paper === true ? 'NOT LIVE' : 'UNKNOWN'}
          </strong>{' · '}
          <strong style={{ color: data?.mode?.auto_execute ? k.text : k.dim }}>
            {data?.mode?.auto_execute ? 'ALGO ARMED' : 'ALGO OFF'}
          </strong>
          {data?.mode?.is_paper === false && data?.mode?.live_execution_enabled === false
            ? <> · <strong style={{ color: k.red }}>LIVE ENTRY BLOCKED</strong></>
            : null}
          {(data?.config?.enabled ?? data?.strategy?.enabled) !== false ? '' : ' · disabled'}
        </span>
        <span
          data-testid="gamma-row-counts"
          style={{ fontSize: 10.5, color: k.dim, marginLeft: 'auto' }}
          title="Watch contracts are selected at the reference 09:20/13:15 builds. E1 is the breakout event; only 4/4 ENTRY is executable."
        >
          {watchCount} watches · {e1Count} E1 · {entryCount} ENTRY · {strongCount} STRONG
          {data?.scan?.recovered_slot ? ` · recovered ${data.scan.recovered_slot}` : ''}
        </span>
        {armedRows.length > 0 && (
          <span style={{ fontSize: 11, color: armableCount > 0 ? k.green : k.red }}>
            {data?.historical
              ? `${armedRows.length} historical ENTRY signal${armedRows.length === 1 ? '' : 's'}`
              : armableCount > 0
                ? `${armableCount} armable of ${armedRows.length} ENTRY signal${armedRows.length === 1 ? '' : 's'}`
                : `${armedRows.length} ENTRY signal${armedRows.length === 1 ? '' : 's'} · execution blocked`}
          </span>
        )}
      </div>

      <GammaSessionTimeline
        days={data?.history_days ?? []}
        selectedDay={selectedDay}
        today={data?.today}
        onSelect={(day) => {
          setSelectedDay(day);
          setOpenId(null);
        }}
      />

      <GammaHistoryLedger
        rows={replay ? [] : history.data?.signals ?? []}
        loading={history.isLoading}
        error={history.error}
        onSelect={(day, signalId) => {
          setSelectedDay(day === data?.today ? '' : day);
          setOpenId(signalId);
        }}
      />

      {data?.historical && (
        <p style={{
          ...note,
          color: data.source === 'live_recorded' ? k.blue : k.amber,
          borderBottom: `1px solid ${k.border}`,
        }}>
          {data.source === 'live_recorded'
            ? `Historical Gamma-Unwind signals for ${data.day} · read-only · captured from the live runtime`
            : data.source === 'historical_replay'
              ? `Historical Gamma-Unwind signals for ${data.day} · read-only · reconstructed from available broker minute data`
              : data.is_trading_day === false
                ? `${data.day} is not an NSE trading day. Latest session: ${data.latest_trading_day}.`
                : `Loading historical Gamma-Unwind signals for ${data.day}…`}
        </p>
      )}

      {(data?.warnings ?? []).map((warning) => (
        <p key={warning} style={{ ...note, borderBottom: `1px solid ${k.border}` }}>
          {warning}
        </p>
      ))}

      <ScanCost scan={data?.scan} />
      {data?.scan?.recovery_source === 'scheduled_history' && (
        <p style={{ ...note, color: k.blue, borderBottom: `1px solid ${k.border}` }}>
          Today&apos;s watchlist was reconstructed at the missed reference slot {data.scan.recovered_slot};
          live E1 detection continues from the reconstructed state.
        </p>
      )}
      {(data?.scan?.coverage ?? []).slice(0, 3).map((item) => (
        <p key={item} style={{ ...note, color: k.amber }}>
          Scan coverage: {item}
        </p>
      ))}

      {scan.error && (
        <p style={{ ...note, color: k.red }}>Scan failed: {(scan.error as Error).message}</p>
      )}
      {historicalLoad.error && (
        <p style={{ ...note, color: k.red }}>Historical load failed: {(historicalLoad.error as Error).message}</p>
      )}
      {backfill.data?.failed?.length ? (
        <p style={{ ...note, color: k.amber }}>
          Historical coverage: {backfill.data.completed.length} loaded · {backfill.data.failed.length} unavailable.
          {' '}{backfill.data.failed.slice(0, 2).map((item) => `${item.day}: ${item.error}`).join(' · ')}
        </p>
      ) : null}
      {backfill.error && (
        <p style={{ ...note, color: k.red }}>
          Historical background load failed: {(backfill.error as Error).message}
        </p>
      )}
      {(data?.coverage ?? []).slice(0, 5).map((item) => (
        <p key={item} style={{ ...note, color: k.amber }}>
          Historical coverage: {item}
        </p>
      ))}
      {arm.data && !arm.data.ok && (
        <p style={{ ...note, color: k.amber }}>Not entered — {arm.data.message}</p>
      )}
      {arm.error && (
        <p style={{ ...note, color: k.red }}>Entry failed: {(arm.error as Error).message}</p>
      )}

      {signals.length === 0 && blockers.length > 0 && (
        <ul style={{ ...note, paddingLeft: 26 }}>
          {blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
      )}

      {!data?.historical && <Record record={data?.record} />}

      {signals.length > 0 && <BoardFilters view={{ ...view, ...commonColumns }} columns={commonColumns.columns} />}
      <SignalBoard
        isHistoricalSim={replay || data?.historical}
        strategyDetailsInExpansion
        renderTrade={(sig) => {
          const reason = gammaEntryBlockReason(sig.id);
          const eligible = sig.status === 'armed' && !data?.historical && !reason && sig.executionEligible !== false;
          return <button type="button" className="sb-owned-entry" aria-label={`Execute Gamma Unwind ${sig.instrument.symbol}`}
            title={reason || sig.reason || 'Execute this Gamma Unwind ENTRY with strategy-owned protection'}
            disabled={!eligible || arm.isPending}
            onClick={(event) => { event.stopPropagation(); if (eligible) arm.mutate(sig.id); }}>
            {arm.isPending && arm.variables === sig.id ? 'Buying…' : 'Execute'}
          </button>;
        }}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={commonColumns.columns}
        hidden={commonColumns.hidden}
        onReorderColumn={commonColumns.onReorderColumn}
        rowScroll={commonColumns.rowScroll}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        renderDetail={(sig) => (
          <div>
            <BoardTicket signal={sig} tag="GAMMA_UNWIND" allowTrade={false} />
            {sig.status === 'armed' && !data?.historical && (() => {
              const entryBlock = gammaEntryBlockReason(sig.id);
              const blocked = entryBlock != null;
              return (
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
                <button
                  type="button"
                  onClick={() => {
                    if (!blocked) arm.mutate(sig.id);
                  }}
                  disabled={arm.isPending || blocked}
                  title={entryBlock || 'Submit this ENTRY through the Gamma-Unwind execution path.'}
                  style={{
                    margin: '8px 12px', background: 'transparent',
                    border: `1px solid ${blocked ? k.red : k.green}`,
                    color: blocked ? k.red : k.green,
                    borderRadius: 6, padding: '4px 12px', fontSize: 11,
                    cursor: (arm.isPending || blocked) ? 'not-allowed' : 'pointer',
                    opacity: blocked ? 0.7 : 1,
                  }}
                >
                  {blocked
                    ? 'Entry blocked'
                    : arm.isPending
                      ? 'Buying…'
                      : `Buy ${sig.sizing.quantity ?? ''} ${formatInstrument(sig.instrument.symbol)}`}
                </button>
                {blocked && (
                  <span data-testid="gamma-entry-block-reason"
                        style={{ fontSize: 11, color: k.dim, maxWidth: 520 }}>
                    {entryBlock}
                  </span>
                )}
                </div>
              );
            })()}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel={data?.historical
          ? data.source === 'historical_replay'
            ? `No Gamma-Unwind watch or E1 signals were found for ${data.day}.`
            : data.source === 'live_recorded'
              ? `No Gamma-Unwind watch or E1 signals were recorded for ${data.day}.`
              : data.is_trading_day === false
                ? `${data.day} is not a trading day.`
                : `Historical signals for ${data.day} are still loading.`
          : 'No Gamma-Unwind watch or E1 signal right now. Use Scan now to rebuild today\'s watchlist.'}
      />
    </div>
  );
}

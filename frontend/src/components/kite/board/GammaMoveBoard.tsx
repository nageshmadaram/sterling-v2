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
  useArmGammaMove, useGammaMoveScan, useGammaMoveSnapshot,
  useReplayGammaMoveDay,
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
  const tone = record.realised_inr >= 0 ? k.green : k.red;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 18px', padding: '8px 12px',
      fontSize: 11, color: k.dim, borderTop: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Trades <b style={{ color: k.text }}>{record.trades}</b></span>
      <span>Win rate <b style={{ color: k.text }}>
        {record.win_rate == null ? '—' : `${record.win_rate.toFixed(1)}%`}</b></span>
      <span>Realised <b style={{ color: tone }}>₹{record.realised_inr.toFixed(0)}</b></span>
      {record.consecutive_losses > 0 && (
        <span style={{ color: k.amber }}>
          {record.consecutive_losses} consecutive loss{record.consecutive_losses === 1 ? '' : 'es'}
        </span>
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
  const rowActions = useBoardRowActions({ onOpenChart });
  const [selectedDay, setSelectedDay] = React.useState('');
  const snapshot = useGammaMoveSnapshot(
    true,
    selectedDay ? 0 : 3000,
    selectedDay || undefined,
  );
  const scan = useGammaMoveScan();
  const replay = useReplayGammaMoveDay();
  const arm = useArmGammaMove();

  const data = snapshot.data;
  const bootstrapDayRef = React.useRef<string | null>(null);
  const replayedDayRef = React.useRef<string | null>(null);

  // On weekends/holidays, an empty "Today" view is not useful. Move the board
  // to the latest reference trading session, where a cached/live-recorded view
  // can be shown or a read-only reconstruction can be requested.
  React.useEffect(() => {
    if (
      selectedDay
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
    selectedDay,
  ]);

  // An arbitrary trading date can now be reconstructed from broker history.
  // Trigger it once when the selected day has no cached runtime/replay row.
  React.useEffect(() => {
    if (
      !selectedDay
      || !data?.historical
      || data.recorded
      || data.is_trading_day === false
      || !data.replay_available
      || replay.isPending
      || replayedDayRef.current === selectedDay
    ) return;
    replayedDayRef.current = selectedDay;
    replay.mutate({ day: selectedDay });
  }, [
    selectedDay,
    data?.historical,
    data?.is_trading_day,
    data?.recorded,
    data?.is_trading_day,
    data?.replay_available,
    replay.isPending,
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

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => {
            if (selectedDay) {
              replayedDayRef.current = selectedDay;
              replay.mutate({ day: selectedDay, force: data?.source === 'historical_replay' });
            } else {
              scan.mutate();
            }
          }}
          disabled={
            selectedDay
              ? replay.isPending || data?.is_trading_day === false || data?.source === 'live_recorded'
              : scan.isPending || data?.is_trading_day === false
          }
          title={
            selectedDay
              ? data?.source === 'live_recorded'
                ? 'This day contains exact live-recorded Gamma events and is never overwritten by replay.'
                : 'Rebuild this day with the reference engine using available one-minute broker history.'
              : 'Rebuild daily zones, client watchlists and the 5-minute E2 runtime now'
          }
          style={{
            background: 'transparent', border: `1px solid ${k.border}`, color: k.text,
            borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: (
              selectedDay
                ? replay.isPending || data?.is_trading_day === false || data?.source === 'live_recorded'
                : scan.isPending || data?.is_trading_day === false
            ) ? 'not-allowed' : 'pointer',
          }}
        >
          {selectedDay
            ? replay.isPending
              ? 'Replaying…'
              : data?.source === 'historical_replay' ? 'Refresh replay' : 'Replay day'
            : scan.isPending ? 'Scanning…' : 'Scan now'}
        </button>
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
                  {row.day} · {row.signals} E1 · {row.watches} watches · {
                    row.source === 'live_recorded'
                      ? 'recorded'
                      : row.source === 'historical_replay' ? 'replayed' : 'replay available'
                  }
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
              : data?.mode?.is_paper === true ? 'PAPER' : 'UNKNOWN'}
          </strong>{' · '}
          <strong style={{ color: data?.mode?.auto_execute ? k.text : k.dim }}>
            {data?.mode?.auto_execute ? 'AUTO' : 'MANUAL'}
          </strong>
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
          <span style={{ fontSize: 11, color: k.green }}>
            {data?.historical
              ? `${armedRows.length} ${data.source === 'historical_replay' ? 'replayed' : 'recorded'} ENTRY signal${armedRows.length === 1 ? '' : 's'}`
              : `${armedRows.length} armed — open a row to buy it`}
          </span>
        )}
      </div>

      {data?.historical && (
        <p style={{
          ...note,
          color: data.source === 'live_recorded' ? k.blue : k.amber,
          borderBottom: `1px solid ${k.border}`,
        }}>
          {data.source === 'live_recorded'
            ? `Recorded Gamma-Unwind state for ${data.day} · read-only · exact live runtime events`
            : data.source === 'historical_replay'
              ? `Reconstructed Gamma-Unwind state for ${data.day} · read-only · E1 proxy uses 1-minute closes because historical ticks do not exist`
              : data.is_trading_day === false
                ? `${data.day} is not an NSE trading day. Latest session: ${data.latest_trading_day}.`
                : `No cached Gamma-Unwind state for ${data.day}. Rebuilding from available broker history.`}
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
      {replay.error && (
        <p style={{ ...note, color: k.red }}>Replay failed: {(replay.error as Error).message}</p>
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

      {signals.length > 0 && <BoardFilters view={view} columns={BOARD_COLUMNS} />}
      <SignalBoard
        renderTrade={rowActions.renderTrade}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={BOARD_COLUMNS}
        hidden={view.hidden}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        renderDetail={(sig) => (
          <div>
            <BoardTicket signal={sig} tag="GAMMA_UNWIND" />
            {sig.status === 'armed' && !data?.historical && (
              <button
                type="button"
                onClick={() => arm.mutate(sig.id)}
                disabled={arm.isPending}
                style={{
                  margin: '8px 12px', background: 'transparent',
                  border: `1px solid ${k.green}`, color: k.green,
                  borderRadius: 6, padding: '4px 12px', fontSize: 11, cursor: 'pointer',
                }}
              >
                {arm.isPending ? 'Buying…' : `Buy ${sig.sizing.quantity ?? ''} ${sig.instrument.symbol}`}
              </button>
            )}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel={data?.historical
          ? data.source === 'historical_replay'
            ? `Replay completed with no watch/E1 rows for ${data.day}.`
            : data.source === 'live_recorded'
              ? `No watch/E1 rows were recorded for ${data.day}.`
              : data.is_trading_day === false
                ? `${data.day} is not a trading day.`
                : `No cached rows for ${data.day}; use Replay day if reconstruction did not start automatically.`
          : 'No Gamma-Unwind watch or E1 signal right now. Use Scan now to rebuild today\'s watchlist.'}
      />
    </div>
  );
}

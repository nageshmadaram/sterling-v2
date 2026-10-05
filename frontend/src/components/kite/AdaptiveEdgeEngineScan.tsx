import React from 'react';
import { useAdaptiveEdgeEngineConfig, useArmAdaptiveEdge } from '../../hooks/useAdaptiveEdge';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useReplayActive as useSimActive, useReplayState, useReplayClock } from '../../hooks/useReplayStore';

/* The Master Specification engine's own scan.
   Deliberately not folded into the board beside it: that board renders a
   spot-scan row model built for the moving-average strategy this one replaced,
   and mapping option-contract candidates onto `spotEntry` / `spotSl` / `score`
   would put numbers under headings that do not mean what they say. This shows
   what the engine actually produced, including F-110 eligibility and the reason
   an individual signal is or is not armable on paper. */

interface EngineCandidate extends Record<string, unknown> {
  signal_id?: string;
  symbol?: string;
  strike?: number;
  option_type?: string;
  dte?: number;
  last_price?: number;
  oi?: number;
  entry_ok?: boolean;
  entry_decision?: string;
  reason?: string;
}

interface EngineScan {
  underlyings?: number;
  chains_read?: number;
  listed?: number;
  tradeable?: number;
  candidates?: EngineCandidate[];
  signals?: EngineCandidate[];
  skipped?: Record<string, string>;
  dropped?: Record<string, number>;
  errors?: string[];
}

interface EngineSnapshot {
  readiness?: { executable?: boolean; reason?: string | null; promotion_gate_reason?: string | null };
  scan?: EngineScan;
  mode?: { is_paper?: boolean | null; auto_execute?: boolean; connected?: boolean };
  session?: Record<string, unknown> | null;
  warnings?: string[];
}

interface PositionRow {
  symbol: string;
  underlying: string;
  type: string;
  quantity: number;
  entry: number;
  stop: number;
  target: number | null;
  state: string;
  open: boolean;
  exit_price: number;
  exit_reason: string;
  /* False means this process is the only thing watching the position. Survivable,
     but the operator has to be able to tell. */
  broker_stop: boolean;
  gtt_id?: number;
  gtt_at?: number;
  broker_stop_qty?: number;
  stop_mode: string;
}

const MUTED = 'var(--k-muted, #8b949e)';
const TEXT = 'var(--k-text, #e6edf3)';
const LINE = 'var(--k-line, #30363d)';

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 74 }}>
      <span style={{ color: MUTED, fontSize: 10.5, letterSpacing: 0.3, textTransform: 'uppercase' }}>{label}</span>
      <span style={{ color: TEXT, fontSize: 14, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  );
}

export function AdaptiveEdgeEngineScan() {
  const isSimActive = useSimActive();
  const replayState = useReplayState();
  const replayClock = useReplayClock();
  const pollInterval = isSimActive ? (replayState === 'running' ? 300 : 2000) : 10000;

  const config = useAdaptiveEdgeEngineConfig();
  const snapshot = useQuery<EngineSnapshot>({
    queryKey: ['adaptive-edge-engine-snapshot', isSimActive ? replayClock : 'live'],
    queryFn: () => api.get<EngineSnapshot>('/api/v1/config/adaptive-edge/snapshot'),
    refetchInterval: pollInterval,
  });

  const qc = useQueryClient();
  const positions = useQuery<{ positions: PositionRow[]; realised_pnl_today: number }>({
    queryKey: ['adaptive-edge-engine-positions', isSimActive ? replayClock : 'live'],
    queryFn: () => api.get('/api/v1/config/adaptive-edge/positions'),
    refetchInterval: pollInterval,
  });
  const arm = useArmAdaptiveEdge();
  const squareOff = useMutation({
    mutationFn: () => api.post('/api/v1/config/adaptive-edge/square-off', {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['adaptive-edge-engine-positions'] });
      qc.invalidateQueries({ queryKey: ['adaptive-edge-engine-snapshot'] });
    },
  });

  const held = (positions.data?.positions ?? []).filter((p) => p.open);
  const realised = positions.data?.realised_pnl_today ?? 0;
  const staleProtection = held.filter(
    (p) => !p.broker_stop && p.stop_mode !== 'monitor' && (p.gtt_id ?? 0) > 0,
  );
  const absentProtection = held.filter(
    (p) => !p.broker_stop && p.stop_mode !== 'monitor' && (p.gtt_id ?? 0) <= 0,
  );

  const scan = snapshot.data?.scan;
  // Signals are the scanner candidates after F-105/F-110. Prefer them so the
  // table shows the actual armability decision instead of a stale blanket label.
  const candidates = scan?.signals?.length ? scan.signals : (scan?.candidates ?? []);
  const dropped = Object.entries(scan?.dropped ?? {});
  const skipped = Object.entries(scan?.skipped ?? {});

  return (
    <section style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <header style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 13, color: TEXT }}>Engine scan</h3>
        <span style={{ color: MUTED, fontSize: 11.5 }}>
          {config.data?.strategy.name ?? 'Adaptive Edge'} · replay only
        </span>
      </header>

      {config.data && !config.data.strategy.validated ? (
        <p style={{ margin: 0, color: MUTED, fontSize: 11.5, lineHeight: 1.5 }}>
          {config.data.strategy.headline_finding}
        </p>
      ) : null}

      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
        <Stat label="Underlyings" value={scan?.underlyings ?? '—'} />
        <Stat label="Chains" value={scan?.chains_read ?? '—'} />
        <Stat label="Listed" value={scan?.listed ?? '—'} />
        <Stat label="Tradeable" value={scan?.tradeable ?? '—'} />
        <Stat label="Candidates" value={candidates.length} />
      </div>

      {/* An empty board has several very different causes, so it says which. */}
      {candidates.length === 0 ? (
        <div style={{ color: MUTED, fontSize: 11.5, lineHeight: 1.6 }}>
          {skipped.length === 0 && dropped.length === 0 ? (
            <span>No scan has run yet, or the session window is closed.</span>
          ) : (
            <>
              {skipped.map(([name, reason]) => (
                <div key={name}>{name}: {reason}</div>
              ))}
              {dropped.map(([reason, count]) => (
                <div key={reason}>{count} contract{count === 1 ? '' : 's'} dropped — {reason}</div>
              ))}
            </>
          )}
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 11.5, width: '100%' }}>
            <thead>
              <tr style={{ color: MUTED, textAlign: 'left' }}>
                {['Contract', 'Strike', 'Type', 'DTE', 'Premium', 'OI', 'Entry'].map((h) => (
                  <th key={h} style={{ padding: '4px 10px 4px 0', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {candidates.slice(0, 25).map((row, index) => (
                <tr key={String(row.symbol ?? index)} style={{ color: TEXT, borderTop: `1px solid ${LINE}` }}>
                  <td style={{ padding: '5px 10px 5px 0' }}>{String(row.symbol ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{String(row.strike ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0' }}>{String(row.option_type ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{String(row.dte ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{String(row.last_price ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{String(row.oi ?? '—')}</td>
                  <td style={{ padding: '5px 10px 5px 0', color: MUTED }}>
                    {row.entry_ok && row.signal_id ? (
                      snapshot.data?.mode?.is_paper === true ? (
                        <button
                          type="button"
                          onClick={() => arm.mutate(String(row.signal_id))}
                          disabled={arm.isPending}
                          style={{
                            fontSize: 11, padding: '3px 8px', cursor: 'pointer',
                            background: 'transparent', color: 'var(--k-green, #3fb950)',
                            border: '1px solid color-mix(in srgb, var(--k-green, #3fb950) 55%, transparent)',
                            borderRadius: 5,
                          }}
                        >
                          {arm.isPending ? 'Arming…' : 'Arm replay'}
                        </button>
                      ) : snapshot.data?.mode?.is_paper === false ? (
                        <span title={snapshot.data?.readiness?.promotion_gate_reason ?? 'strategy_promotion_required'}>
                          Live entry unavailable
                        </span>
                      ) : (
                        <span>Broker mode unknown — reconnect the account</span>
                      )
                    ) : (
                      <span title={String(row.entry_decision ?? 'NO_TRADE')}>
                        {String(row.reason || 'F-110 entry gate not yet satisfied')}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {held.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: `1px solid ${LINE}`, paddingTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <strong style={{ color: TEXT, fontSize: 12 }}>Open positions</strong>
            <span style={{ color: MUTED, fontSize: 11.5, fontVariantNumeric: 'tabular-nums' }}>
              realised today {realised >= 0 ? '+' : ''}{Math.round(realised).toLocaleString('en-IN')}
            </span>
            <button
              type="button"
              onClick={() => squareOff.mutate()}
              disabled={squareOff.isPending}
              style={{ marginLeft: 'auto', fontSize: 11.5, padding: '4px 10px', cursor: 'pointer',
                       background: 'transparent', color: TEXT, border: `1px solid ${LINE}`, borderRadius: 5 }}
            >
              {squareOff.isPending ? 'Squaring off…' : 'Square off all'}
            </button>
          </div>

          {/* Named, not implied by a missing badge: an operator scanning the row
              should not have to notice an absence. */}
          {staleProtection.length > 0 ? (
            <p style={{ margin: 0, color: 'var(--k-warn, #d29922)', fontSize: 11.5 }}>
              {staleProtection.length} position{staleProtection.length === 1 ? ' has' : 's have'} a broker trigger
              that is not synchronized to the current stop/quantity. Reconcile protection before treating
              {staleProtection.length === 1 ? ' it' : ' them'} as fully broker protected.
            </p>
          ) : null}
          {absentProtection.length > 0 ? (
            <p style={{ margin: 0, color: 'var(--k-warn, #d29922)', fontSize: 11.5 }}>
              {absentProtection.length} position{absentProtection.length === 1 ? ' has' : 's have'} no broker stop —
              this process is the only thing watching {absentProtection.length === 1 ? 'it' : 'them'}.
            </p>
          ) : null}

          <table style={{ borderCollapse: 'collapse', fontSize: 11.5, width: '100%' }}>
            <thead>
              <tr style={{ color: MUTED, textAlign: 'left' }}>
                {['Contract', 'Qty', 'Entry', 'Stop', 'Target', 'Protection'].map((h) => (
                  <th key={h} style={{ padding: '4px 10px 4px 0', fontWeight: 500 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {held.map((row) => (
                <tr key={row.symbol} style={{ color: TEXT, borderTop: `1px solid ${LINE}` }}>
                  <td style={{ padding: '5px 10px 5px 0' }}>{row.symbol}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{row.quantity}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{row.entry}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{row.stop}</td>
                  <td style={{ padding: '5px 10px 5px 0', fontVariantNumeric: 'tabular-nums' }}>{row.target ?? '—'}</td>
                  <td style={{ padding: '5px 10px 5px 0', color: row.broker_stop ? TEXT : 'var(--k-warn, #d29922)' }}>
                    {row.broker_stop
                      ? 'Broker stop'
                      : (row.gtt_id ?? 0) > 0 ? 'Broker stop stale' : 'This process only'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {arm.data && !arm.data.ok ? (
        <p style={{ margin: 0, color: 'var(--k-warn, #d29922)', fontSize: 11.5 }}>
          Entry refused: {arm.data.reason ?? arm.data.status ?? 'entry gate refused'}
        </p>
      ) : null}
      {arm.error ? (
        <p style={{ margin: 0, color: 'var(--k-danger, #f85149)', fontSize: 11.5 }}>
          Entry failed: {(arm.error as Error).message}
        </p>
      ) : null}

      {snapshot.data?.readiness?.executable === false ? (
        <p style={{ margin: 0, color: MUTED, fontSize: 11.5 }}>
          Live execution blocked: {snapshot.data.readiness.promotion_gate_reason ?? snapshot.data.readiness.reason}
        </p>
      ) : null}

      {(scan?.errors ?? []).map((error) => (
        <p key={error} style={{ margin: 0, color: 'var(--k-danger, #f85149)', fontSize: 11.5 }}>{error}</p>
      ))}
    </section>
  );
}

export default AdaptiveEdgeEngineScan;

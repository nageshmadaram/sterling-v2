import { useEffect, useState } from 'react';
import { useViewStore, useView } from '../../store/useViewStore';
import {
  describeSimError, isOwnerDenied, simRunState, simulatedStrategies, strategyLabel, useNow, useSimStatus,
  type SimStatus,
} from '../../hooks/useSim';
import { fmtMoney, fmtPnl, pnlColor } from '../../lib/format';
import { SimStatusCard } from './SimStatusCard';
import { RunChip } from './RunChip';
import { SIM, button } from './simStyle';

/**
 * What the shell shows under the header for the view that is open.
 *
 *  - SIMULATION: a persistent amber banner. It says in words that the money is virtual and no real
 *    order is sent, and it carries the three numbers a person looks at first (cash, equity, today).
 *  - LIVE: nothing, unless some strategy is in Simulation mode; then one small blue chip that
 *    takes you to the Simulation view. A real-money screen stays quiet otherwise.
 */
export function SimBanner() {
  return useView() === 'simulation' ? <VirtualBanner /> : <SimModeChip />;
}

function Figure({ label, value, color, hint }: { label: string; value: string; color?: string; hint: string }) {
  return (
    <div title={hint} style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.6, color: SIM.ink, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.15, color: color ?? SIM.text, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  );
}

function VirtualBanner() {
  const setView = useViewStore((s) => s.setView);
  const q = useSimStatus();
  const now = useNow();
  const [open, setOpen] = useState(false);
  const status: SimStatus | undefined = q.data;

  // A remembered Simulation view that this login may not open: go back to the real one.
  const denied = q.isError && isOwnerDenied(q.error);
  useEffect(() => { if (denied) setView('live'); }, [denied, setView]);

  return (
    <section
      role="region"
      aria-label="Simulation"
      data-testid="sim-banner"
      style={{
        flexShrink: 0, background: SIM.tint, color: SIM.text,
        borderBottom: `3px solid ${SIM.accent}`, padding: '10px 20px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px 24px', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 800, color: SIM.ink, letterSpacing: 0.3 }}>
            SIMULATION — virtual money, no real orders
          </div>
          <div style={{ fontSize: 11.5, color: SIM.ink, opacity: 0.85, marginTop: 2 }}>
            Everything here is practice. Your real Zerodha account is not touched.
          </div>
        </div>

        {status ? (
          <div style={{ display: 'flex', gap: '6px 28px', flexWrap: 'wrap' }}>
            <Figure label="Virtual cash" value={fmtMoney(status.cash)} hint="Virtual money not tied up in open trades." />
            <Figure label="Equity" value={fmtMoney(status.equity)} hint="Cash plus what the open trades are worth right now." />
            <Figure label="Today's P&L" value={fmtPnl(status.today_pnl)} color={pnlColor(status.today_pnl)}
              hint="Profit or loss so far today, after charges, including trades still open." />
          </div>
        ) : (
          <div role="status" style={{ flex: '1 1 260px', fontSize: 12, color: SIM.ink }}>
            {q.isError ? describeSimError(q.error).text : 'Loading the virtual account…'}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {status && <RunChip state={simRunState(status, now)} />}
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls="sim-details"
            style={button(SIM.ink)}>
            {open ? 'Hide details' : 'Details'}
          </button>
          <button type="button" onClick={() => setView('live')} style={button(SIM.live)}>
            Back to LIVE
          </button>
        </div>
      </div>

      {open && (
        <div id="sim-details" style={{ marginTop: 12 }}>
          <SimStatusCard status={status} />
        </div>
      )}
    </section>
  );
}

function SimModeChip() {
  const setView = useViewStore((s) => s.setView);
  const { data } = useSimStatus();
  const running = simulatedStrategies(data);
  if (running.length === 0) return null;

  const names = running.map(strategyLabel).join(', ');
  return (
    <div data-testid="sim-mode-chip-row" style={{ flexShrink: 0, padding: '5px 20px', display: 'flex' }}>
      <button
        type="button"
        onClick={() => setView('simulation')}
        title={`Simulation is trading ${names} with virtual money. Open the Simulation view.`}
        aria-label={`Simulation is on for ${names}. Open the Simulation view.`}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 7, minHeight: 28, padding: '3px 11px', borderRadius: 999,
          border: `1px solid ${SIM.info}`, background: SIM.infoTint, color: SIM.info,
          fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: 'pointer',
        }}
      >
        <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: SIM.info }} />
        {running.length === 1 ? `${names} is in Simulation` : `${running.length} strategies in Simulation`}
        <span aria-hidden>→</span>
      </button>
    </div>
  );
}


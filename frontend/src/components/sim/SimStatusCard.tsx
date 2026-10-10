import { useId, useState, type FormEvent, type ReactNode } from 'react';
import {
  describeSimError, simRunState, useNow, useResetSimulation, useSetSimCapital, type SimStatus,
} from '../../hooks/useSim';
import { fmtMoney, fmtPnl, fmtQty, fmtTimeIST, pnlColor } from '../../lib/format';
import { notifyOrder } from '../../store/useKiteNotifications';
import { RunChip } from './RunChip';
import { SimDialog } from './SimDialog';
import { SIM, TAP, button } from './simStyle';

/** The server's own ceiling (`MAX_CAPITAL` in api/v1/endpoints/sim.py). */
export const MAX_VIRTUAL_CAPITAL = 10_000_000_000;

function Tile({ label, value, note, color }: { label: string; value: ReactNode; note: string; color?: string }) {
  return (
    <div style={{ minWidth: 0, padding: '10px 12px', background: SIM.surface, border: `1px solid ${SIM.border}`, borderRadius: 8 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.6, color: SIM.muted, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.2, margin: '2px 0', color: color ?? SIM.text, fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>
        {value}
      </div>
      <div style={{ fontSize: 11, lineHeight: 1.4, color: SIM.faint }}>{note}</div>
    </div>
  );
}

/** "Rs 5,00,000" or "5000000" or "5,00,000.50" -> a number, or null when it is not one. */
export function parseCapital(raw: string): number | null {
  const cleaned = raw.replace(/[₹,\s]/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

export function SimStatusCard({ status }: { status?: SimStatus }) {
  const now = useNow();
  const setCapital = useSetSimCapital();
  const reset = useResetSimulation();
  const capitalId = useId();
  const [editing, setEditing] = useState(false);
  const [capitalText, setCapitalText] = useState('');
  const [capitalError, setCapitalError] = useState('');
  const [confirmReset, setConfirmReset] = useState(false);
  const [resetNote, setResetNote] = useState<{ ok: boolean; text: string } | null>(null);

  if (!status) {
    return <div role="status" style={{ fontSize: 12, color: SIM.muted }}>Loading the virtual account…</div>;
  }

  const run = simRunState(status, now);

  const startEdit = () => {
    setCapitalText(String(Math.round(status.capital)));
    setCapitalError('');
    setEditing(true);
  };

  const saveCapital = (e: FormEvent) => {
    e.preventDefault();
    const value = parseCapital(capitalText);
    if (value === null || value <= 0) { setCapitalError('Enter an amount in rupees, like 500000.'); return; }
    if (value > MAX_VIRTUAL_CAPITAL) { setCapitalError(`The most allowed is ${fmtMoney(MAX_VIRTUAL_CAPITAL, 0)}.`); return; }
    setCapitalError('');
    setCapital.mutate(value, {
      onSuccess: () => {
        setEditing(false);
        notifyOrder({ kind: 'info', title: 'Virtual capital changed', message: `Virtual capital is now ${fmtMoney(value)}.` });
      },
      onError: (err) => setCapitalError(describeSimError(err).text),
    });
  };

  const doReset = () => {
    reset.mutate(undefined, {
      onSuccess: (res) => {
        setConfirmReset(false);
        const failed = Object.keys(res?.failed ?? {});
        setResetNote(failed.length === 0
          ? { ok: true, text: 'The simulation was reset. Virtual cash is back to the starting capital.' }
          : { ok: false, text: `Reset, but some parts could not be cleared: ${failed.join(', ')}. Try again.` });
        notifyOrder({ kind: 'info', title: 'Simulation reset', message: 'All virtual trades were cleared.' });
      },
      onError: (err) => {
        setConfirmReset(false);
        setResetNote({ ok: false, text: describeSimError(err).text });
      },
    });
  };

  return (
    <div data-testid="sim-status-card" style={{ background: SIM.sunken, border: `1px solid ${SIM.border}`, borderRadius: 10, padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: 0.6, color: SIM.text }}>VIRTUAL ACCOUNT</div>
        <RunChip state={run} />
        <span style={{ fontSize: 11.5, color: SIM.muted, flex: '1 1 200px' }}>{run.detail}</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
        <Tile label="Capital" value={fmtMoney(status.capital)} note="The virtual money the simulation started with." />
        <Tile label="Cash" value={fmtMoney(status.cash)} note="Virtual money not tied up in open trades." />
        <Tile label="Equity" value={fmtMoney(status.equity)} note="Cash plus what the open trades are worth right now." />
        <Tile label="Today's P&L" value={fmtPnl(status.today_pnl)} color={pnlColor(status.today_pnl)}
          note="Profit or loss so far today, after charges." />
        <Tile label="Open positions" value={fmtQty(status.open_positions)} note="Trades that are still running." />
        <Tile label="Last price check"
          value={status.runtime?.last_pass_ms ? `${fmtTimeIST(status.runtime.last_pass_ms)} IST` : '—'}
          note="When the simulation last looked at market prices." />
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        <button type="button" onClick={startEdit} style={button(SIM.info)} disabled={editing}>Set virtual capital</button>
        <button type="button" onClick={() => setConfirmReset(true)} style={button(SIM.danger)}>Reset simulation</button>
      </div>

      {editing && (
        <form onSubmit={saveCapital} style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ flex: '1 1 200px', minWidth: 0 }}>
            <label htmlFor={capitalId} style={{ display: 'block', fontSize: 11.5, color: SIM.muted, marginBottom: 4 }}>
              Virtual capital (₹)
            </label>
            <input
              id={capitalId}
              inputMode="decimal"
              value={capitalText}
              onChange={(e) => setCapitalText(e.target.value)}
              aria-invalid={capitalError ? true : undefined}
              aria-describedby={`${capitalId}-help`}
              autoFocus
              style={{
                width: '100%', boxSizing: 'border-box', minHeight: TAP, padding: '0 10px', borderRadius: 7,
                border: `1px solid ${capitalError ? SIM.danger : SIM.border}`, background: SIM.surface, color: SIM.text,
                fontFamily: 'inherit', fontSize: 14,
              }}
            />
            <div id={`${capitalId}-help`} style={{ fontSize: 11, color: SIM.faint, marginTop: 4, lineHeight: 1.4 }}>
              The daily loss limits follow it: the simulation stops for the day at a 2% loss and warns at 1%.
            </div>
            {capitalError && <div role="alert" style={{ fontSize: 12, color: SIM.danger, marginTop: 4 }}>{capitalError}</div>}
          </div>
          <div style={{ display: 'flex', gap: 8, paddingTop: 20 }}>
            <button type="submit" disabled={setCapital.isPending} style={button(SIM.info, true)}>
              {setCapital.isPending ? 'Saving…' : 'Save'}
            </button>
            <button type="button" onClick={() => setEditing(false)} style={button(SIM.muted)}>Cancel</button>
          </div>
        </form>
      )}

      {resetNote && (
        <div role={resetNote.ok ? 'status' : 'alert'} style={{ marginTop: 10, fontSize: 12, color: resetNote.ok ? SIM.live : SIM.danger }}>
          {resetNote.text}
        </div>
      )}

      {confirmReset && (
        <SimDialog
          title="Reset simulation"
          accent={SIM.danger}
          busy={reset.isPending}
          typedPhrase="RESET"
          confirmLabel={reset.isPending ? 'Resetting…' : 'Reset simulation'}
          onCancel={() => setConfirmReset(false)}
          onConfirm={doReset}
        >
          This wipes every <strong>virtual</strong> order, position, trade and journal entry and puts the virtual
          cash back to the starting capital. It cannot be undone. Your real Zerodha account is not touched.
        </SimDialog>
      )}
    </div>
  );
}

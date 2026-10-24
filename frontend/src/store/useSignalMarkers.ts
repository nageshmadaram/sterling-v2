import { create } from 'zustand';

// ─── Signal markers (✝ best R:R, ▲ best delta) shared across panes ──────────────
// The Sterling Kite Engine rows compute, per signal, which option leg is the best
// reward:risk (✝) and which has the highest delta (▲). Those same markers should
// follow the instrument anywhere it appears — the market watchlist and the ticker
// — so a user who pins/adds a marked contract sees the same ✝/▲.
//
// Each board publishes its owned signal/mode rows in one atomic batch;
// unchanged refreshes are no-ops and unmounts remove only that board’s rows. The flat `markers` map — keyed by the
// full `EXCHANGE:tradingsymbol` symbol (the same string the watchlist/ticker use)
// — is what consumers read.

export interface Marker { rr?: boolean; delta?: boolean }

interface SignalMarkersState {
  /** owned signal identity -> { fullSymbol -> Marker } */
  byRow: Record<string, Record<string, Marker>>;
  /** flattened fullSymbol -> Marker (OR-merged across rows) */
  markers: Record<string, Marker>;
  publish: (rowKey: string, entries: Record<string, Marker>) => void;
  clear: (rowKey: string) => void;
  /** Atomically replace the caller's previous rows, retaining other publishers. */
  replaceRows: (previousKeys: readonly string[], entries: Record<string, Record<string, Marker>>) => void;
}

function sameEntries(a: Record<string, Marker> | undefined, b: Record<string, Marker>): boolean {
  if (!a) return false;
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (!b[k] || a[k].rr !== b[k].rr || a[k].delta !== b[k].delta) return false;
  }
  return true;
}

function flatten(byRow: Record<string, Record<string, Marker>>, previous: Record<string, Marker>): Record<string, Marker> {
  const out: Record<string, Marker> = {};
  for (const row of Object.values(byRow)) {
    for (const [sym, m] of Object.entries(row)) {
      const cur = out[sym];
      out[sym] = { rr: cur?.rr || m.rr, delta: cur?.delta || m.delta };
    }
  }
  for (const key of Object.keys(out)) {
    const old = previous[key];
    if (old && old.rr === out[key].rr && old.delta === out[key].delta) out[key] = old;
  }
  return sameEntries(previous, out) ? previous : out;
}

export const useSignalMarkers = create<SignalMarkersState>((set, get) => ({
  byRow: {},
  markers: {},
  publish: (rowKey, entries) => get().replaceRows([], { [rowKey]: entries }),
  clear: (rowKey) => get().replaceRows([rowKey], {}),
  replaceRows: (previousKeys, entries) => set((state) => {
    let byRow = state.byRow;
    const writable = () => {
      if (byRow === state.byRow) byRow = { ...byRow };
    };
    for (const key of previousKeys) {
      if (!(key in entries) && key in byRow) {
        writable();
        delete byRow[key];
      }
    }
    for (const [key, markers] of Object.entries(entries)) {
      if (!sameEntries(byRow[key], markers)) {
        writable();
        byRow[key] = markers;
      }
    }
    if (byRow === state.byRow) return state;
    return { byRow, markers: flatten(byRow, state.markers) };
  }),
}));

import { api } from "../../utils/api";
import { PCR_SNAPSHOT_ISO, snapshotSeries } from "./snapshot";
import { PCR_INDICES, type PcrDeskPayload, type PcrIndex, type PcrSeries } from "./types";

const STORE = "sterling.pcr.books.v1";

/** Older mobile caches and partial feeds may omit marks or the spot object. */
export function normalizePcrPayload(value: unknown): PcrDeskPayload | null {
  if (!value || typeof value !== 'object') return null;
  const input = value as Partial<PcrDeskPayload>;
  if (!input.series || typeof input.series !== 'object') return null;
  const series = {} as Record<PcrIndex, PcrSeries>;
  for (const index of PCR_INDICES) {
    const row = input.series[index.id];
    if (!row || typeof row !== 'object') continue;
    series[index.id] = {
      ...row, id: index.id,
      expiry: typeof row.expiry === 'string' ? row.expiry : '',
      spot: { ltp: null, changePer: null, maxPain: null, vix: null, timestamp: null, ...(row.spot as Partial<PcrSeries["spot"]> || {}) },
      marks: Array.isArray(row.marks) ? row.marks.filter(mark => mark && typeof mark.hhmm === 'string') : [],
      latest: row.latest && typeof row.latest.hhmm === 'string' ? row.latest : null,
    };
  }
  if (!Object.keys(series).length) return null;
  return { asOf: typeof input.asOf === 'string' ? input.asOf : '', source: input.source === 'live' ? 'live' : 'snapshot', series };
}

function snapshotPayload(): PcrDeskPayload {
  const series = {} as Record<PcrIndex, PcrSeries>;
  for (const row of PCR_INDICES) series[row.id] = snapshotSeries(row.id);
  return { asOf: `${PCR_SNAPSHOT_ISO}T15:30:00`, source: "snapshot", series };
}

export function sessionIsoOf(payload: PcrDeskPayload): string {
  const asOf = payload.asOf || "";
  const ts = payload.series?.NIFTY?.spot?.timestamp || "";
  // asOf is the scrape clock; prefer it so a stale spot.timestamp cannot pin
  // the desk to yesterday while today's prints are already in the payload.
  const raw = asOf || ts;
  return raw.slice(0, 10);
}

function readBooks(): Record<string, PcrDeskPayload> {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, PcrDeskPayload>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeBooks(books: Record<string, PcrDeskPayload>) {
  try {
    localStorage.setItem(STORE, JSON.stringify(books));
  } catch {
    /* quota */
  }
}

export function rememberSession(payload: PcrDeskPayload) {
  const iso = sessionIsoOf(payload);
  if (!iso) return;
  const books = readBooks();
  books[iso] = payload;
  writeBooks(books);
}

export function recallSession(iso: string): PcrDeskPayload | null {
  if (iso === PCR_SNAPSHOT_ISO) return snapshotPayload();
  return normalizePcrPayload(readBooks()[iso]);
}

function emptyPayload(iso: string): PcrDeskPayload {
  return { asOf: `${iso}T00:00:00`, source: "snapshot", series: {} as Record<PcrIndex, PcrSeries> };
}

export async function fetchPcrDesk(sessionIso?: string | null): Promise<PcrDeskPayload> {
  try {
    const data = normalizePcrPayload(await api.get<unknown>("/api/v1/pcr/session"));
    if (data?.series && Object.keys(data.series).length) {
      rememberSession(data);
      const liveIso = sessionIsoOf(data);
      if (!sessionIso || sessionIso === liveIso) return data;
    }
  } catch {
    /* fall through to stored / snapshot */
  }
  if (sessionIso) {
    const stored = recallSession(sessionIso);
    if (stored) return stored;
    return emptyPayload(sessionIso);
  }
  return snapshotPayload();
}

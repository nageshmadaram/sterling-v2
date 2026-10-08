/**
 * One place for how Sterling prints money, P&L, quantities and times.
 *
 * Conventions (every board, pane and toast should follow them):
 *  - money    -> "₹1,234.50"   (Indian grouping, always 2 decimals)
 *  - P&L      -> "+₹1,234.50" / "-₹43.75" / "₹0.00", coloured by sign
 *  - times    -> "HH:MM:SS" 24h in IST, whatever the browser's zone
 *  - missing  -> "—", never "NaN", "null" or "0" standing in for unknown
 *
 * Pure functions only, so they are safe in tables, tooltips and toasts alike.
 */

export const EM_DASH = '—';

/** The short labels every surface uses for the same concept. */
export const LABELS = {
  entry: 'Entry',
  ltp: 'LTP',
  sl: 'SL',
  tsl: 'TSL',
  target: 'Target',
  exit: 'Exit',
  pnl: 'P&L',
  qty: 'Qty',
  risk: 'Risk',
  capital: 'Capital',
  status: 'Status',
} as const;

export type LabelKey = keyof typeof LABELS;

const IST = 'Asia/Kolkata';

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function toNumber(v: unknown): number | null {
  if (finite(v)) return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function group(abs: number, decimals: number): string {
  return abs.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** Plain price/number with fixed decimals and Indian grouping: 24,512.35 */
export function fmtPx(value: unknown, decimals = 2): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  const body = group(Math.abs(n), decimals);
  // -0.001 rounds to "0.00": do not print a sign in front of nothing.
  const isZero = Number(body.replace(/,/g, '')) === 0;
  return n < 0 && !isZero ? `-${body}` : body;
}

/** Rupee amount: ₹1,234.50 (negative: -₹43.75) */
export function fmtMoney(value: unknown, decimals = 2): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  const body = group(Math.abs(n), decimals);
  const isZero = Number(body.replace(/,/g, '')) === 0;
  return n < 0 && !isZero ? `-₹${body}` : `₹${body}`;
}

/** Signed rupee P&L: +₹1,234.50 / -₹43.75 / ₹0.00 */
export function fmtPnl(value: unknown, decimals = 2): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  const body = group(Math.abs(n), decimals);
  const isZero = Number(body.replace(/,/g, '')) === 0;
  if (isZero) return `₹${body}`;
  return n > 0 ? `+₹${body}` : `-₹${body}`;
}

export type PnlTone = 'gain' | 'loss' | 'flat';

export function pnlTone(value: unknown): PnlTone {
  const n = toNumber(value);
  if (n === null) return 'flat';
  const rounded = Math.round(n * 100) / 100;
  if (rounded > 0) return 'gain';
  if (rounded < 0) return 'loss';
  return 'flat';
}

/** CSS colour for a P&L value (theme tokens, so dark mode follows). */
export function pnlColor(value: unknown): string {
  const tone = pnlTone(value);
  if (tone === 'gain') return 'var(--k-green)';
  if (tone === 'loss') return 'var(--k-red)';
  return 'var(--k-dim)';
}

/** Whole-number quantity with Indian grouping: 1,800 */
export function fmtQty(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  return Math.round(n).toLocaleString('en-IN');
}

/** Percentage: 12.50% (pass signed:true for +12.50%) */
export function fmtPct(value: unknown, decimals = 2, signed = false): string {
  const n = toNumber(value);
  if (n === null) return EM_DASH;
  const body = Math.abs(n).toFixed(decimals);
  const isZero = Number(body) === 0;
  if (isZero) return `${body}%`;
  if (n < 0) return `-${body}%`;
  return signed ? `+${body}%` : `${body}%`;
}

/** Accepts epoch ms, epoch seconds, an ISO string or a Date. */
function toDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === 'string' && value.trim() !== '' && toNumber(value) === null) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const n = toNumber(value);
  if (n === null || n <= 0) return null;
  // Anything below 1e11 is epoch SECONDS (1e11 s is the year 5138; 1e11 ms is 1973).
  const ms = n < 1e11 ? n * 1000 : n;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? null : d;
}

const TIME_FMT = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
const TIME_HM_FMT = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const DATE_FMT = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST, day: '2-digit', month: 'short',
});

/** IST wall-clock time: 09:15:03 (pass seconds:false for 09:15) */
export function fmtTimeIST(value: unknown, seconds = true): string {
  const d = toDate(value);
  if (!d) return EM_DASH;
  return (seconds ? TIME_FMT : TIME_HM_FMT).format(d);
}

/** IST date + time: 05 Oct 09:15:03 */
export function fmtDateTimeIST(value: unknown, seconds = true): string {
  const d = toDate(value);
  if (!d) return EM_DASH;
  return `${DATE_FMT.format(d)} ${fmtTimeIST(d, seconds)}`;
}

/** Elapsed seconds as a compact age: 12s / 4m / 3h / 2d */
export function fmtAgeSeconds(seconds: unknown): string {
  const n = toNumber(seconds);
  if (n === null || n < 0) return EM_DASH;
  if (n < 60) return `${Math.floor(n)}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m`;
  if (n < 86400) return `${Math.floor(n / 3600)}h`;
  return `${Math.floor(n / 86400)}d`;
}

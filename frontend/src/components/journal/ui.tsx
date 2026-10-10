/**
 * Small shared pieces for the Journal: section frames, badges, a segmented control and the three
 * states every data block has (loading, error, empty). Colours are theme tokens only, so light, dark
 * and grey all follow the app.
 */
import React from 'react';
import { fmtPnl, pnlColor } from '../../lib/format';
import { modeBadge } from './journalModel';

/**
 * ``container-type`` makes the Journal respond to the room it is given (a narrow dock or a phone),
 * not to the window. Under 640 px a table becomes a stack of cards: the same markup, restyled, so a
 * screen reader and a test see one table either way.
 */
export const JOURNAL_CSS = `
.jr-root { container-type: inline-size; container-name: jr; }
.jr-root button { font-family: inherit; }
.jr-root button:focus-visible, .jr-root input:focus-visible, .jr-root select:focus-visible,
.jr-root summary:focus-visible, .jr-root [tabindex]:focus-visible { outline: 2px solid var(--k-brand); outline-offset: 2px; }
.jr-scroll { overflow-x: auto; }
.jr-rt { width: 100%; border-collapse: collapse; font-size: 13px; }
.jr-rt th { text-align: left; font-weight: 500; color: var(--k-dim); font-size: 11px; padding: 6px 8px; white-space: nowrap; border-bottom: 1px solid var(--k-border); }
.jr-rt td { padding: 8px; border-bottom: 1px solid var(--k-surface-hover); vertical-align: middle; }
.jr-rt .jr-num { text-align: right; font-variant-numeric: tabular-nums; }
.jr-rt th.jr-num { text-align: right; }
.jr-trades { min-width: 940px; }
.jr-grid-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 10px; }
.jr-two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 24px; }
@container jr (max-width: 959px) {
  .jr-trades { min-width: 700px; font-size: 12px; }
  .jr-trades th, .jr-trades td { padding: 6px; }
  .jr-rt .jr-hide-medium { display: none; }
}
@container jr (max-width: 639px) {
  .jr-two { grid-template-columns: 1fr; }
}
@container jr (max-width: 759px) {
  .jr-scroll { overflow-x: visible; }
  .jr-trades { min-width: 0; font-size: 13px; }
  .jr-rt thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .jr-rt, .jr-rt tbody, .jr-rt tr, .jr-rt td { display: block; width: 100%; box-sizing: border-box; }
  .jr-rt tr { border: 1px solid var(--k-border); border-radius: 10px; margin-bottom: 10px; padding: 6px 12px; background: var(--k-bg); }
  .jr-rt tr.jr-detail-row { padding: 0; border: 0; background: transparent; margin-top: -6px; }
  .jr-rt td { display: flex; justify-content: space-between; align-items: baseline; gap: 14px; text-align: right; border-bottom: 0; padding: 4px 0; }
  .jr-rt td::before { content: attr(data-label); color: var(--k-dim); font-size: 12px; font-weight: 400; text-align: left; flex: 0 0 auto; }
  .jr-rt td.jr-first { padding-bottom: 6px; border-bottom: 1px solid var(--k-surface-hover); margin-bottom: 2px; }
  .jr-rt td.jr-first::before, .jr-rt td.jr-wide::before { content: none; }
  .jr-rt td.jr-wide { display: block; text-align: left; }
  .jr-rt .jr-num { text-align: right; }
  .jr-rt td.jr-hide-medium { display: flex; }
  .jr-rt td.jr-hide-narrow { display: none; }
  .jr-rt td.jr-detail { display: block; padding: 0; text-align: left; }
  .jr-rt td.jr-detail::before { content: none; }
}
@media (prefers-reduced-motion: reduce) { .jr-root * { transition: none !important; animation: none !important; } }
`;

export function JournalStyles() {
  return <style>{JOURNAL_CSS}</style>;
}

export const card: React.CSSProperties = {
  background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 10, padding: 14, minWidth: 0,
};

export const dimText: React.CSSProperties = { color: 'var(--k-dim)', fontSize: 12, lineHeight: 1.45 };

export const controlStyle: React.CSSProperties = {
  height: 32, padding: '0 10px', borderRadius: 8, border: '1px solid var(--k-border-strong)',
  background: 'var(--k-bg)', color: 'var(--k-text)', fontSize: 13, minWidth: 0,
};

export function Section({ id, title, subtitle, actions, children }: {
  id: string; title: string; subtitle?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
}) {
  const headingId = `jr-h-${id}`;
  return (
    <section aria-labelledby={headingId} data-testid={`journal-${id}`} style={{ ...card, marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={{ minWidth: 0 }}>
          <h2 id={headingId} style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--k-ink-1)' }}>{title}</h2>
          {subtitle && <div style={{ ...dimText, marginTop: 2 }}>{subtitle}</div>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

// ─── Badges and chips ──────────────────────────────────────────────────────────────────────────

type Tone = 'neutral' | 'good' | 'bad' | 'warn' | 'live' | 'sim';
const TONES: Record<Tone, { fg: string; bg: string }> = {
  neutral: { fg: 'var(--k-ink-3)', bg: 'var(--k-surface-hover)' },
  good: { fg: 'var(--k-green-deep)', bg: 'var(--k-tint-green)' },
  bad: { fg: 'var(--k-red-deep)', bg: 'var(--k-tint-red)' },
  warn: { fg: 'var(--k-warn-ink)', bg: 'var(--k-tint-amber)' },
  live: { fg: 'var(--k-brand-deep)', bg: 'var(--k-tint-warm)' },
  sim: { fg: 'var(--k-violet)', bg: 'var(--k-surface-slate)' },
};

export function Chip({ tone = 'neutral', children, title }: { tone?: Tone; children: React.ReactNode; title?: string }) {
  const t = TONES[tone];
  return (
    <span title={title} style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
      color: t.fg, background: t.bg, whiteSpace: 'nowrap', lineHeight: 1.5,
    }}
    >{children}</span>
  );
}

/** LIVE (real money) and SIMULATION (virtual money) always read differently at a glance. */
export function ModeBadge({ mode }: { mode: string }) {
  const m = modeBadge(mode);
  return (
    <Chip tone={m === 'LIVE' ? 'live' : 'sim'} title={m === 'LIVE' ? 'Real money, real orders' : 'Virtual money, no real orders'}>
      {m}
    </Chip>
  );
}

export function PnlText({ value, decimals = 2, style }: { value: number | null | undefined; decimals?: number; style?: React.CSSProperties }) {
  return <span style={{ color: pnlColor(value), fontVariantNumeric: 'tabular-nums', ...style }}>{fmtPnl(value, decimals)}</span>;
}

// ─── Segmented control ─────────────────────────────────────────────────────────────────────────

export function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: ReadonlyArray<{ value: T; label: string }>; onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label} style={{ display: 'inline-flex', border: '1px solid var(--k-border-strong)', borderRadius: 8, overflow: 'hidden', flexWrap: 'nowrap' }}>
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            style={{
              height: 32, padding: '0 12px', border: 'none', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap',
              borderLeft: i ? '1px solid var(--k-border-strong)' : 'none',
              background: on ? 'var(--k-brand)' : 'var(--k-bg)', color: on ? 'var(--k-on-accent)' : 'var(--k-text)',
              fontWeight: on ? 600 : 400,
            }}
          >{o.label}</button>
        );
      })}
    </div>
  );
}

// ─── Loading / error / empty ───────────────────────────────────────────────────────────────────

export function Loading({ what }: { what: string }) {
  return (
    <div role="status" aria-busy="true" style={{ ...dimText, padding: '18px 4px' }}>Loading {what}…</div>
  );
}

/** The backend answers errors as JSON text ({"code","message"}); show its sentence, not the JSON. */
export function friendlyError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? '');
  try {
    const parsed = JSON.parse(raw) as { message?: unknown };
    if (parsed && typeof parsed.message === 'string' && parsed.message) return parsed.message;
  } catch { /* plain text */ }
  return raw || 'Something went wrong.';
}

export function ErrorBlock({ what, error, onRetry }: { what: string; error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" style={{
      padding: '12px 14px', borderRadius: 8, background: 'var(--k-tint-red)', color: 'var(--k-red-deep)',
      border: '1px solid var(--k-red)', fontSize: 13, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
    }}
    >
      <span style={{ flex: '1 1 220px' }}>Could not load {what}. {friendlyError(error)}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} style={{ ...controlStyle, cursor: 'pointer', fontWeight: 600 }}>Try again</button>
      )}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div data-testid="journal-empty" style={{
      padding: '18px 16px', borderRadius: 8, background: 'var(--k-surface)', border: '1px dashed var(--k-border-strong)',
      textAlign: 'left',
    }}
    >
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--k-ink-1)' }}>{title}</div>
      {children && <div style={{ ...dimText, marginTop: 4, fontSize: 13 }}>{children}</div>}
      {action && <div style={{ marginTop: 10 }}>{action}</div>}
    </div>
  );
}

/** Shown when a refresh failed but older numbers are still on screen: never pass them off as current. */
export function StaleNote({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div role="status" style={{ ...dimText, color: 'var(--k-warn-ink)', background: 'var(--k-tint-amber)', borderRadius: 6, padding: '4px 10px', marginBottom: 8 }}>
      Could not refresh just now. These are the last numbers received.
    </div>
  );
}

/** Dims content while a refresh is in flight so the numbers never look current when they are not. */
export function Refreshing({ on, children }: { on: boolean; children: React.ReactNode }) {
  return <div aria-busy={on} style={{ opacity: on ? 0.6 : 1, transition: 'opacity .15s' }}>{children}</div>;
}

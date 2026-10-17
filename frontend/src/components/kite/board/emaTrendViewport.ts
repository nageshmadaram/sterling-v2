import type { BoardSignal } from './boardTypes';

/** Bound React/DOM cost while preserving each selected profile's evidence.
 * Active/unresolved inventory bypasses every view and paging limit. */
export function selectEmaViewport(
  filtered: readonly BoardSignal[],
  all: readonly BoardSignal[],
  limit: number,
): BoardSignal[] {
  const ordered = [...filtered].sort((a, b) => (b.atMs || 0) - (a.atMs || 0));
  const cap = Math.max(0, limit);
  const profiles = [...new Set(ordered.map(row => row.emaTrend?.mode).filter(Boolean))];
  const selected = new Set<string>();
  // The freshest ultra-scalp rows must not hide every swing/intraday signal.
  // Reserve a small fixed share for each populated profile, then fill by time.
  if (profiles.length > 1 && cap >= profiles.length) {
    const perProfile = Math.max(1, Math.floor(cap / (profiles.length * 4)));
    for (const profile of profiles) {
      let count = 0;
      for (const row of ordered) {
        if (row.emaTrend?.mode !== profile) continue;
        selected.add(row.id);
        if (++count >= perProfile) break;
      }
    }
  }
  for (const row of ordered) {
    if (selected.size >= cap) break;
    selected.add(row.id);
  }
  const page = ordered.filter(row => selected.has(row.id));
  for (const row of all) {
    if (!row.emaTrend?.campaignId || row.status === 'ended' || selected.has(row.id)) continue;
    page.unshift(row);
    selected.add(row.id);
  }
  return page;
}

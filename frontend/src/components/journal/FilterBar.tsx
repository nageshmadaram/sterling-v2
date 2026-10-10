import React from 'react';
import { type JournalFilters, type RangePreset } from './journalModel';
import { STRATEGY_IDS, STRATEGY_NAMES, type JournalMode } from './types';
import { Segmented, controlStyle, dimText } from './ui';

const MODE_OPTIONS: ReadonlyArray<{ value: JournalMode; label: string }> = [
  { value: 'live', label: 'Live' },
  { value: 'simulation', label: 'Simulation' },
  { value: 'all', label: 'All' },
];

const RANGE_OPTIONS: ReadonlyArray<{ value: RangePreset; label: string }> = [
  { value: 'today', label: 'Today' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Pick dates' },
];

const MODE_NOTE: Record<JournalMode, string> = {
  live: 'Live: real trades on the Zerodha account, real money.',
  simulation: 'Simulation: practice trades with virtual money. No real orders are sent.',
  all: 'All: Live (real money) and Simulation (virtual money) added together. The totals mix the two.',
};

export function FilterBar({ filters, patch, modes }: {
  filters: JournalFilters;
  patch: (p: Partial<JournalFilters>) => void;
  /** The views this person may read; the rest are not offered. */
  modes: readonly JournalMode[];
}) {
  return (
    <div data-testid="journal-filters" style={{ display: 'grid', gap: 8, marginBottom: 14 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        <Segmented label="Which trades" value={filters.mode} options={MODE_OPTIONS.filter((o) => modes.includes(o.value))} onChange={(mode) => patch({ mode })} />
        <Segmented label="Time period" value={filters.range} options={RANGE_OPTIONS} onChange={(range) => patch({ range })} />
        <select
          aria-label="Strategy" value={filters.strategy} style={{ ...controlStyle, minWidth: 150 }}
          onChange={(e) => patch({ strategy: e.target.value as JournalFilters['strategy'] })}
        >
          <option value="">All strategies</option>
          {STRATEGY_IDS.map((id) => <option key={id} value={id}>{STRATEGY_NAMES[id]}</option>)}
        </select>
      </div>
      {filters.range === 'custom' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', fontSize: 13 }}>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>From
            <input type="date" aria-label="From date" value={filters.dateFrom} max={filters.dateTo || undefined}
              onChange={(e) => patch({ dateFrom: e.target.value })} style={controlStyle} />
          </label>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>To
            <input type="date" aria-label="To date" value={filters.dateTo} min={filters.dateFrom || undefined}
              onChange={(e) => patch({ dateTo: e.target.value })} style={controlStyle} />
          </label>
        </div>
      )}
      <div data-testid="mode-note" style={dimText}>{MODE_NOTE[filters.mode]}</div>
    </div>
  );
}

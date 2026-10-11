import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { SignalBoard, type ColumnId } from '../SignalBoard';
import type { BoardSignal, EngineId } from '../boardTypes';

const NOW = Date.UTC(2026, 7, 21, 5);
const COLUMNS: ColumnId[] = ['instrument', 'exchange', 'entry', 'stop', 'trail', 'exitState', 'target', 'ltp', 'time', 'trade', 'chart'];
function signal(engine: EngineId, id = engine): BoardSignal {
  return {
    id, engine, underlying: 'BANKNIFTY', direction: 'long', status: 'armed', atMs: NOW,
    instrument: { symbol: 'BANKNIFTY26NOV55200CE', exchange: 'NFO', kind: 'option', lotSize: 30, quoteKey: 'NFO:BANKNIFTY26NOV55200CE' },
    levels: { ltp: 1415.2, entry: 1373, stop: 1174.05, trail: 1331.25, target: 1770.9, exit: null },
    sizing: { lots: 1, quantity: 30, atRiskInr: 5968.5, deployedInr: 41190 }, score: null,
    origin: { label: `${engine} evidence`, hint: 'Strategy-specific measurements', tone: 'dim' },
    sections: [{ title: 'Strategy evidence', stats: [{ label: 'Observation', value: 'confirmed' }] }],
    reason: 'Valid setup',
  };
}
function mount(rows: BoardSignal[], openId: string | null = null, extra = {}) {
  return render(React.createElement(SignalBoard, {
    signals: rows, columns: COLUMNS, openId, onToggle: vi.fn(), nowMs: NOW,
    rowScroll: true, strategyDetailsInExpansion: true, ...extra,
  }));
}

describe('rendered release-strategy table consistency', () => {
  it('keeps the same headers, order, cell widths and type on all three engines', () => {
    const layouts = (['supertrend', 'snapback', 'gamma_move'] as EngineId[]).map(engine => {
      const view = mount([signal(engine)]);
      const headers = [...view.container.querySelectorAll('.sb-head')].map(el => el.textContent);
      const cells = [...view.container.querySelectorAll('.sb-row [data-cell]')].map(el => ({
        key: el.getAttribute('data-cell'), width: (el as HTMLElement).style.width,
        size: (el as HTMLElement).style.fontSize,
      }));
      expect(cells.map(cell => cell.key)).toEqual(COLUMNS);
      expect(view.getByLabelText('BANKNIFTY NOV 55200 CE')).toBeInTheDocument();
      view.unmount();
      return { headers, cells };
    });
    expect(layouts[1]).toEqual(layouts[0]);
    expect(layouts[2]).toEqual(layouts[0]);
  });

  it('keeps strategy evidence out of collapsed rows and reveals it on expansion', () => {
    const sig = signal('snapback');
    const view = mount([sig]);
    expect(view.container.textContent).not.toContain('snapback evidence');
    expect(view.container.textContent).not.toContain('Observation');
    view.unmount();
    const expanded = mount([sig], sig.id);
    expect(expanded.container.textContent).toContain('snapback evidence');
    expect(expanded.container.textContent).toContain('Observation');
    expect(expanded.container.textContent).toContain('confirmed');
  });

  it('reserves the same accent gutter when a row opens', () => {
    const sig = signal('supertrend');
    const view = mount([sig]);
    const closed = view.container.querySelector('.sb-row') as HTMLElement;
    expect(closed.style.borderLeftWidth).toBe('3px');
    view.unmount();
    const opened = mount([sig], sig.id).container.querySelector('.sb-row') as HTMLElement;
    expect(opened.style.borderLeftWidth).toBe('3px');
    expect(opened.style.borderLeftColor).not.toBe('transparent');
  });

  it('keeps the full column set on a narrow board with sideways scrolling', () => {
    const view = mount([signal('gamma_move')]);
    const row = view.container.querySelector('.sb-row-scroll') as HTMLElement;
    expect(row.style.overflowX).toBe('auto');
    expect(row.querySelectorAll('[data-cell]')).toHaveLength(COLUMNS.length);
    const head = view.container.querySelector('.sb-head-row') as HTMLElement;
    expect(head.style.position).toBe('sticky');
    expect(head.style.top).toBe('0px');
  });

  it('opens the contract chart without also expanding or raising an order', () => {
    const chart = vi.fn(), toggle = vi.fn();
    const view = mount([signal('gamma_move')], null, {
      onToggle: toggle,
      renderChart: (sig: BoardSignal) => React.createElement('button', {
        onClick: (event: React.MouseEvent) => { event.stopPropagation(); chart(sig.instrument.quoteKey); },
      }, 'Chart'),
    });
    fireEvent.click(view.container.querySelector('.sb-row [data-cell=chart] button')!);
    expect(chart).toHaveBeenCalledWith('NFO:BANKNIFTY26NOV55200CE');
    expect(toggle).not.toHaveBeenCalled();
  });
});

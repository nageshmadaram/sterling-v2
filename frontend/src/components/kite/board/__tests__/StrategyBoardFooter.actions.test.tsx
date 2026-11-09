import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { StrategyBoardFooter } from '../StrategyBoardFooter';

afterEach(cleanup);

function renderStrategy(name: string, onScan = vi.fn()) {
  render(<StrategyBoardFooter name={name}>
    <div>
      <button type="button" onClick={onScan}>Scan now</button>
      <span>Zero names scanned · no broker orders</span>
      <a href="#settings" onClick={event => event.preventDefault()}>Settings</a>
      <details><summary>Scan modes</summary>Existing view only</details>
    </div>
  </StrategyBoardFooter>);
  return onScan;
}

describe('compact strategy header controls', () => {
  it.each(['EMA Trend', 'Snapback', 'Gamma Unwind'])(
    '%s keeps its primary actions mounted, keyboard accessible and operative',
    name => {
      const onScan = renderStrategy(name);
      const toolbar = screen.getByRole('region', { name: `${name} actions` });
      const scan = within(toolbar).getByRole('button', { name: 'Scan now' });
      const settings = within(toolbar).getByRole('link', { name: 'Settings' });
      expect(scan).toBeVisible();
      expect(settings).toBeVisible();
      expect(toolbar).toContainElement(within(toolbar).getByText('Scan modes'));
      fireEvent.click(scan);
      expect(onScan).toHaveBeenCalledOnce();
      expect(screen.getByText('Zero names scanned · no broker orders')).toBeVisible();
      expect(toolbar).toHaveStyle({ overflowX: 'auto', flexWrap: 'nowrap' });
    },
  );

  it('retains Gamma history access without hiding the scan control', () => {
    renderStrategy('Gamma Unwind');
    expect(screen.getByRole('button', { name: 'Gamma history and coverage' })).toBeVisible();
    expect(screen.getByRole('region', { name: 'Gamma Unwind actions' }))
      .toContainElement(screen.getByRole('button', { name: 'Scan now' }));
  });

  it('keeps Gamma history date unique when the modal is open and restores it on close', () => {
    const onScan = vi.fn();
    render(<StrategyBoardFooter name="Gamma Unwind">
      <div>
        <button type="button" onClick={onScan}>Scan now</button>
        <label>History date <input aria-label="Gamma history date" type="date" defaultValue="2026-10-02" /></label>
      </div>
    </StrategyBoardFooter>);
    expect(screen.getAllByLabelText('Gamma history date')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Gamma history and coverage' }));
    const dialog = screen.getByRole('dialog', { name: 'Gamma history and coverage' });
    expect(within(dialog).getByLabelText('Gamma history date')).toBeVisible();
    expect(screen.getAllByLabelText('Gamma history date')).toHaveLength(1);
    const toolbar = screen.getByRole('region', { name: 'Gamma Unwind actions' });
    expect(within(toolbar).queryByLabelText('Gamma history date')).toBeNull();
    fireEvent.click(within(toolbar).getByRole('button', { name: 'Scan now' }));
    expect(onScan).toHaveBeenCalledOnce();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close strategy options' }));
    expect(screen.queryByRole('dialog', { name: 'Gamma history and coverage' })).toBeNull();
    expect(within(toolbar).getByLabelText('Gamma history date')).toBeVisible();
  });

  it('leaves the SuperTrend header layout unchanged', () => {
    renderStrategy('SuperTrend');
    expect(screen.getByRole('region', { name: 'SuperTrend controls and information' }))
      .toContainElement(screen.getByRole('button', { name: 'Scan now' }));
    expect(screen.queryByRole('region', { name: 'SuperTrend actions' })).toBeNull();
  });
});

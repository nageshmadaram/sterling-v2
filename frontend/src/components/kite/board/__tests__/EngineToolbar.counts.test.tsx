import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EngineTabs } from '../EngineToolbar';

describe('engine tab count meaning', () => {
  it('does not turn a zero-signal board into zero scanned instruments', () => {
    render(<EngineTabs tabs={[{ id: 'snapback', running: true, live: 0, loadedRecords: 0 }]}
      active="snapback" onSelect={() => {}} />);
    expect(screen.getByRole('tab', { name: 'Snapback, enabled, 0 active rows, 0 loaded records' })).toBeVisible();
    expect(screen.getByRole('tab')).not.toHaveAccessibleName(/scanned/);
  });

  it('describes retained records without claiming a fresh scan or executable entries', () => {
    render(<EngineTabs tabs={[{ id: 'ema_trend', running: true, live: 7, loadedRecords: 200 }]}
      active="ema_trend" onSelect={() => {}} />);
    expect(screen.getByRole('tab', { name: 'EMA Trend, enabled, 7 active rows, 200 loaded records' })).toBeVisible();
    expect(screen.getByRole('tab')).toHaveAttribute('title', expect.stringContaining('not scan coverage or execution eligibility'));
  });

  it('keeps retained record counts available when the engine is disabled', () => {
    render(<EngineTabs tabs={[{ id: 'snapback', running: false, live: 0, loadedRecords: 12 }]}
      active="snapback" onSelect={() => {}} />);
    expect(screen.getByRole('tab', { name: 'Snapback, disabled, 0 active rows, 12 loaded records' })).toBeVisible();
  });
});

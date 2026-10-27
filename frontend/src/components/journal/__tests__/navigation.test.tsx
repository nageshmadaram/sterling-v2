/**
 * The Journal has to be reachable from everywhere a person can navigate, and has to survive a reload as
 * the default page. KiteTab is a whole workspace to mount, so (as the other navigation contracts do) its
 * routing is asserted against the source; the rail, the settings picker and the persisted setting are
 * rendered for real.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {NAV_OPTIONS} from '../../../store/useExperienceOptions';
import kiteTab from '../../kite/KiteTab.tsx?raw';
import kiteLayout from '../../kite/KiteLayout.tsx?raw';
import palette from '../../kite/KiteCommandPalette.tsx?raw';
import simpleSettings from '../../SimpleSettings.tsx?raw';

import { KiteActivityRail } from '../../kite/KiteActivityRail';
import { DefaultSectionSettings } from '../../kite/DefaultSectionSettings';
import { useKiteSettings } from '../../../store/useKiteSettings';

describe('Journal navigation', () => {
  it('is in the top navigation, right after Positions and before More', () => {
    const ids=NAV_OPTIONS.map(([id])=>id);expect(ids.indexOf('journal')).toBe(ids.indexOf('positions')+1);expect(ids.indexOf('more')).toBe(ids.indexOf('journal')+1);
  });

  it('is a section the layout knows, and KiteTab renders the Journal tab for it', () => {
    expect(kiteLayout).toMatch(/export type NavItem = [^;]*'journal'/);
    expect(kiteTab).toMatch(/import\('\.\.\/journal\/JournalTab'\)/);
    expect(kiteTab).toMatch(/nav === 'journal'\) content = <JournalTab \/>/);
  });

  it('is in the command palette and the default-page picker', () => {
    expect(palette).toMatch(/id: 'nav-journal'[^\n]*onNavClick\('journal'\)/);
    expect(simpleSettings).toMatch(/value: 'journal', label: 'Journal'/);
  });
});

describe('activity rail', () => {
  const props = { activeNav: 'dashboard' as const, onNavClick: vi.fn(), onOpenCommandPalette: vi.fn(), onOpenCustomizeLayout: vi.fn() };

  it('has a Journal button after the trading pages, and it navigates', () => {
    const onNavClick = vi.fn();
    render(<KiteActivityRail {...props} onNavClick={onNavClick} />);
    const ids = screen.getAllByTestId(/^rail-/).map((e) => e.getAttribute('data-testid'));
    expect(ids.indexOf('rail-journal')).toBe(ids.indexOf('rail-positions') + 1);
    fireEvent.click(screen.getByRole('button', { name: 'Journal' }));
    expect(onNavClick).toHaveBeenCalledWith('journal');
  });

  it('marks the Journal active when it is the current page', () => {
    render(<KiteActivityRail {...props} activeNav="journal" />);
    expect(screen.getByRole('button', { name: 'Journal' })).toHaveStyle({ color: 'var(--k-brand)' });
  });
});

describe('default page setting', () => {
  beforeEach(() => { useKiteSettings.setState({ defaultSection: 'dashboard' }); });

  it('can be set to the Journal', () => {
    render(<DefaultSectionSettings />);
    fireEvent.click(screen.getByLabelText(/Journal/i));
    fireEvent.click(screen.getByRole('button', { name: /Apply changes/i }));
    expect(useKiteSettings.getState().defaultSection).toBe('journal');
  });

  it('is kept, not reset to the dashboard, when the saved settings are loaded again', async () => {
    localStorage.setItem('kite-settings', JSON.stringify({ version: 8, state: { defaultSection: 'journal' } }));
    vi.resetModules();
    const fresh = (await import('../../../store/useKiteSettings')).useKiteSettings;
    expect(fresh.getState().defaultSection).toBe('journal');
  });
});

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

vi.mock('../../components/kite/KiteTab', () => ({
  KiteTab: () => <div data-testid="kite-trading-docks">Trading docks</div>,
}));
vi.mock('../../components/tv/TvWorkspace', () => ({
  TvWorkspace: () => <div data-testid="fake-tv-content">TV content</div>,
}));
vi.mock('../../components/kite/ProfileMenu', () => ({
  ProfileMenu: () => null,
  useSterlingProfile: () => ({ data: { username: 'operator' } }),
}));
vi.mock('../../components/SterlingLogo', () => ({
  SterlingLogo: () => <span>Logo</span>,
}));
vi.mock('../../components/kite/ThemeToggle', () => ({
  ThemeToggle: () => null,
}));
vi.mock('../../components/sim/ViewSwitch', () => ({
  ViewSwitch: () => <span>Broker mode</span>,
}));
vi.mock('../../components/sim/SimBanner', () => ({
  SimBanner: () => <span>Simulation banner</span>,
}));
vi.mock('../../components/kite/MobileExperienceMenu', () => ({
  MobileExperienceMenu: () => <div>Trading mobile navigation</div>,
}));
vi.mock('../../components/SimpleSettings', () => ({
  SimpleSettingsDrawer: () => null,
}));
vi.mock('../../store/useExperienceOptions', () => ({
  useExperienceOptions: (selector: (s: { options: Record<string, unknown> }) => unknown) =>
    selector({ options: {} }),
  orderedNavigation: (items: unknown[]) => items,
}));
vi.mock('../../components/kite/navigationDensity', () => ({
  groupPrimaryNavigation: (items: unknown[]) => ({ primary: items, overflow: [] }),
}));
vi.mock('../../store/useKiteSettings', () => {
  const settings = { defaultSection: 'dashboard' };
  const hook = Object.assign(
    (selector: (s: typeof settings) => unknown) => selector(settings),
    { getState: () => settings },
  );
  return { useKiteSettings: hook };
});

import { SimpleTerminal } from '../SimpleTerminal';
import { resetDraftGuard, setDraftDirty } from '../../components/kite/config/unsavedDraftGuard';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetDraftGuard();
});
afterEach(() => {
  cleanup();
  resetDraftGuard();
  vi.restoreAllMocks();
});

describe('Sterling Spectrum product navigation', () => {
  it('puts TV next to KITE under the Sterling header and keeps KITE the default', () => {
    render(<SimpleTerminal />);
    const tabs = screen.getByRole('tablist', { name: 'Sterling spectrum' });
    expect(tabs).toBeInTheDocument();
    const buttons = screen.getAllByRole('tab');
    expect(buttons.map(button => button.textContent)).toEqual(['KITE', 'TV']);
    expect(screen.getByTestId('spectrum-kite-tab')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('kite-trading-docks')).toBeInTheDocument();
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
  });

  it('gives TV all space beneath the header without trading rails or banners', () => {
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('spectrum-tv-tab')).toHaveAttribute('aria-selected', 'true');
    expect(document.getElementById('sterling-tv-surface')?.getAttribute('style')).toContain('flex: 1');
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(screen.queryByText('Simulation banner')).toBeNull();
    expect(screen.queryByText('Trading mobile navigation')).toBeNull();
    const kite = document.getElementById('sterling-kite-surface')!;
    expect(kite).toHaveStyle({ display: 'none' });
    expect(screen.getByTestId('kite-trading-docks')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
    expect(kite).toHaveStyle({ display: 'flex' });
  });

  it('guards unsaved trading settings before entering TV', () => {
    render(<SimpleTerminal />);
    setDraftDirty('settings', true);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('spectrum-kite-tab')).toHaveAttribute('aria-selected', 'true');
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('spectrum-tv-tab')).toHaveAttribute('aria-selected', 'true');
  });
});

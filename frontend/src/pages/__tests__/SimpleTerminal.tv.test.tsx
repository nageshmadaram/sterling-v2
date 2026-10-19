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
vi.mock('../../components/sim/SimBanner', () => ({
  SimBanner: () => <span>Simulation banner</span>,
}));
vi.mock('../../components/kite/MobileExperienceMenu', () => ({
  MobileExperienceMenu: () => <div>Trading mobile navigation</div>,
}));
vi.mock('../../hooks/useSim', () => ({
  useSimStatus: () => ({ isSuccess: true, isError: false }),
  isOwnerDenied: () => false,
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

import { useExperienceOptions, readExperienceOptions } from '../../store/useExperienceOptions';
import { useViewStore } from '../../store/useViewStore';
import { SimpleTerminal } from '../SimpleTerminal';
import { resetDraftGuard, setDraftDirty } from '../../components/kite/config/unsavedDraftGuard';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetDraftGuard();
  useViewStore.setState({ view: 'live' });
  useExperienceOptions.setState({ options: readExperienceOptions() });
});
afterEach(() => {
  cleanup();
  resetDraftGuard();
  vi.restoreAllMocks();
});

describe('Sterling Spectrum product navigation', () => {
  it('restores the original KITE switch styling beside Sterling; TV matches it', () => {
    render(<SimpleTerminal />);
    const nav = document.querySelector('.sterling-header-row')!;
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole('switch', {name:'Live trading view'})).toHaveTextContent('KITE');
    expect(screen.getByRole('switch', {name:'Live trading view'}))
      .toHaveClass('mobile-view-toggle','unified-view-toggle');
    expect(screen.getByRole('button', {name:'TV'}))
      .toHaveClass('mobile-view-toggle','unified-view-toggle');
    expect(Array.from(nav.querySelectorAll(':scope > .mobile-view-toggle'))
      .map(button => button.textContent)).toEqual(['KITE','TV']);
    expect(screen.getByTestId('kite-trading-docks')).toBeInTheDocument();
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
  });

  it('gives TV the full screen below the original header without trading controls', () => {
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('spectrum-tv-tab')).toHaveAttribute('aria-current','page');
    expect(document.getElementById('sterling-tv-surface')?.getAttribute('style'))
      .toContain('flex: 1');
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(screen.queryByText('Simulation banner')).toBeNull();
    expect(screen.queryByText('Trading mobile navigation')).toBeNull();
    const kite = document.getElementById('sterling-kite-surface')!;
    expect(kite).toHaveStyle({display:'none'});
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
    expect(kite).toHaveStyle({display:'flex'});
    expect(screen.getByRole('switch', {name:'Live trading view'})).toBeInTheDocument();
  });

  it('guards unsaved settings before entering TV', () => {
    render(<SimpleTerminal />);
    setDraftDirty('settings', true);
    const confirm = vi.spyOn(window,'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('kite-trading-docks')).toBeInTheDocument();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('shows KITE, Simulation and renamed TV separately in the requested order', () => {
    useExperienceOptions.getState().update({
      spectrumLayout:'separate',
      tvLabel:'My Channels',
      spectrumOrder:['tv','kite','simulation'],
    });
    render(<SimpleTerminal />);
    const buttons=Array.from(document.querySelectorAll(
      '.sterling-header-row > .mobile-view-toggle.unified-view-toggle'
    )).map(button=>button.textContent);
    expect(buttons).toEqual(['My Channels','KITE','Simulation']);
    fireEvent.click(screen.getByTestId('spectrum-simulation-tab'));
    expect(useViewStore.getState().view).toBe('simulation');
    expect(screen.getByTestId('spectrum-simulation-tab')).toHaveAttribute('aria-current','page');
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    expect(useViewStore.getState().view).toBe('live');
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(screen.getByTestId('spectrum-tv-tab')).toHaveTextContent('My Channels');
  });

  it('cycles KITE → Simulation → renamed TV → KITE using the chosen order', () => {
    useExperienceOptions.getState().update({
      spectrumLayout:'cycle', tvLabel:'Cinema',
    });
    render(<SimpleTerminal />);
    const next = () => fireEvent.click(screen.getByTestId('spectrum-cycle-switch'));
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('KITE');
    next();
    expect(useViewStore.getState().view).toBe('simulation');
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('Simulation');
    next();
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('Cinema');
    next();
    expect(useViewStore.getState().view).toBe('live');
    expect(screen.getByTestId('fake-tv-content')).not.toBeInTheDocument();
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('KITE');
  });

  it('cycles only through enabled destinations', () => {
    useExperienceOptions.getState().update({
      spectrumLayout:'cycle',
      spectrumVisible:{kite:true,simulation:false,tv:true},
    });
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-cycle-switch'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(useViewStore.getState().view).toBe('live');
    fireEvent.click(screen.getByTestId('spectrum-cycle-switch'));
    expect(screen.getByTestId('fake-tv-content')).not.toBeInTheDocument();
    expect(useViewStore.getState().view).toBe('live');
  });

  it('keeps a separately shown Simulation workspace accessible even with live KITE hidden', () => {
    useExperienceOptions.getState().update({
      spectrumLayout:'separate',
      spectrumVisible:{kite:false,simulation:true,tv:true},
    });
    render(<SimpleTerminal />);
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-simulation-tab'));
    expect(useViewStore.getState().view).toBe('simulation');
    expect(screen.getByTestId('kite-trading-docks')).toBeInTheDocument();
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('can hide Simulation without losing the KITE or TV destinations', () => {
    useExperienceOptions.getState().update({
      spectrumVisible:{kite:true,simulation:false,tv:true},
    });
    render(<SimpleTerminal />);
    expect(screen.queryByRole('switch',{name:'Live trading view'})).toBeNull();
    expect(screen.getByTestId('spectrum-kite-tab')).toHaveTextContent('KITE');
    expect(screen.getByTestId('spectrum-tv-tab')).toHaveTextContent('TV');
  });

  it('exposes Experience settings while using TV-only navigation', () => {
    useExperienceOptions.getState().update({
      spectrumVisible:{kite:false,simulation:false,tv:true},
    });
    render(<SimpleTerminal />);
    expect(screen.queryByTestId('spectrum-kite-tab')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'Experience settings'}));
    expect(screen.getByRole('region',{name:'Spectrum navigation settings'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'Restore Spectrum defaults'}));
    expect(screen.getByTestId('spectrum-tv-tab')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    expect(screen.getByRole('switch',{name:'Live trading view'})).toBeInTheDocument();
  });
});

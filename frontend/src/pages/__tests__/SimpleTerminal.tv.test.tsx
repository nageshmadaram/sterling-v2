import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

vi.mock('../../components/kite/KiteTab', () => ({
  KiteTab: () => <div data-testid="kite-trading-docks">Trading docks</div>,
}));
vi.mock('../../components/radio/RadioWorkspace', () => ({
  RadioWorkspace: () => <div data-testid="fake-radio-content">Radio content</div>,
}));
vi.mock('../../components/tv/TvWorkspace', () => ({
  TvWorkspace: () => <div data-testid="fake-tv-content">TV content</div>,
}));
vi.mock('../../components/kite/ProfileMenu', () => ({
  ProfileMenu: () => <div data-testid="fake-profile">Profile</div>,
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
  const settings = { defaultSection: 'pcr' };
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
  cleanup(); resetDraftGuard(); vi.restoreAllMocks();
});

describe('Sterling Spectrum navigation and Home', () => {
  it('starts with one KITE toggle and a clickable Sterling Home', () => {
    render(<SimpleTerminal />);
    expect(screen.getByRole('button',{name:'Sterling Home'})).toBeInTheDocument();
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('KITE');
    expect(screen.queryByTestId('spectrum-tv-tab')).toBeNull();
  });

  it('cycles in the chosen order and only through selected workspaces', () => {
    useExperienceOptions.getState().update({
      spectrumOrder:['tv','kite','simulation','radio'],spectrumVisible:{kite:true,simulation:false,tv:true,radio:false},
    });
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-cycle-switch'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-cycle-switch'));
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
    expect(useViewStore.getState().view).toBe('live');
  });

  it('displays selected destination buttons side by side', () => {
    useExperienceOptions.getState().update({
      spectrumLayout:'separate',spectrumOrder:['tv','kite','simulation','radio'],
    });
    render(<SimpleTerminal />);
    const buttons=Array.from(document.querySelectorAll('.sterling-header-row > .mobile-view-toggle'))
      .map(button=>button.textContent);
    expect(buttons).toEqual(['TV','KITE','Simulation','Radio']);
    fireEvent.click(screen.getByTestId('spectrum-simulation-tab'));
    expect(useViewStore.getState().view).toBe('simulation');
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('opens configured KITE Home from TV and dismisses overlays', () => {
    useExperienceOptions.getState().update({spectrumLayout:'separate'});
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    const homeClose=vi.fn();
    window.addEventListener('sterling-home',homeClose);
    fireEvent.click(screen.getByRole('button',{name:'Sterling Home'}));
    expect(homeClose).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('kite-trading-docks')).toBeVisible();
    expect(useViewStore.getState().view).toBe('live');
    window.removeEventListener('sterling-home',homeClose);
  });

  it('does not open hidden KITE after refresh when only TV and Radio are selected', () => {
    useExperienceOptions.getState().update({
      spectrumHome:'kite',
      spectrumVisible:{kite:false,simulation:false,tv:true,radio:true},
      spectrumOrder:['tv','radio','kite','simulation'],
    });
    // Simulates loading the settings again from persistent browser storage.
    useExperienceOptions.setState({ options: readExperienceOptions() });
    render(<SimpleTerminal />);
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    expect(screen.queryByTestId('spectrum-kite-tab')).toBeNull();
    expect(screen.getByTestId('spectrum-cycle-switch')).toHaveTextContent('TV');
    fireEvent.click(screen.getByRole('button',{name:'Sterling Home'}));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('uses the Radio header tab and can set Radio as Home', () => {
    useExperienceOptions.getState().update({spectrumHome:'radio',spectrumLayout:'separate'});
    render(<SimpleTerminal />);
    expect(screen.getByTestId('fake-radio-content')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    fireEvent.click(screen.getByRole('button',{name:'Sterling Home'}));
    expect(screen.getByTestId('fake-radio-content')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Reload Radio'})).toBeInTheDocument();
  });

  it('uses a configured TV landing tab and home setting', () => {
    useExperienceOptions.getState().update({spectrumHome:'tv',spectrumLayout:'separate'});
    render(<SimpleTerminal />);
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    fireEvent.click(screen.getByRole('button',{name:'Sterling Home'}));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('routes mobile Home events to the same setting', () => {
    useExperienceOptions.getState().update({spectrumHome:'tv',spectrumLayout:'separate'});
    render(<SimpleTerminal />);
    fireEvent.click(screen.getByTestId('spectrum-kite-tab'));
    fireEvent(window,new Event('sterling-go-home'));
    expect(screen.getByTestId('fake-tv-content')).toBeInTheDocument();
  });

  it('starts authorized Simulation Home using the existing view store', () => {
    useExperienceOptions.getState().update({spectrumHome:'simulation'});
    render(<SimpleTerminal />);
    expect(useViewStore.getState().view).toBe('simulation');
  });

  it('prevents losing unsaved settings on workspace switch', () => {
    useExperienceOptions.getState().update({spectrumLayout:'separate'});
    render(<SimpleTerminal />);
    setDraftDirty('settings',true);
    const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByTestId('spectrum-tv-tab'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('fake-tv-content')).toBeNull();
  });
});

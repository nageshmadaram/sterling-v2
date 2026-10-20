import React from 'react';
import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { SpectrumSettings } from '../SpectrumSettings';
import {
  EXPERIENCE_KEY, DEFAULT_EXPERIENCE_OPTIONS,
  normalizeExperienceOptions, readExperienceOptions, useExperienceOptions,
} from '../../../store/useExperienceOptions';

beforeEach(() => {
  localStorage.clear();
  useExperienceOptions.setState({ options: readExperienceOptions() });
});
afterEach(() => cleanup());

describe('Spectrum settings in Settings → Experience', () => {
  it('defaults to KITE Home and a single cycling toggle', () => {
    render(<SpectrumSettings />);
    expect(screen.getByRole('combobox', { name:'Default Spectrum home tab' })).toHaveValue('kite');
    expect(screen.getByRole('combobox', { name:'Spectrum navigation layout' })).toHaveValue('cycle');
    expect(screen.getByRole('combobox', { name:'Spectrum navigation layout' }).querySelectorAll('option')).toHaveLength(2);
    for (const name of ['KITE', 'Simulation', 'TV', 'Radio']) {
      expect(screen.getByRole('checkbox', { name:`Show ${name}` })).toBeChecked();
    }
  });

  it('persists configurable Home, buttons, selection and order', () => {
    render(<SpectrumSettings />);
    fireEvent.change(screen.getByRole('combobox',{name:'Default Spectrum home tab'}),{target:{value:'tv'}});
    fireEvent.change(screen.getByRole('combobox',{name:'Spectrum navigation layout'}),{target:{value:'separate'}});
    fireEvent.click(screen.getByRole('checkbox',{name:'Show Simulation'}));
    fireEvent.click(screen.getByRole('button',{name:'Move TV left'}));
    expect(readExperienceOptions()).toMatchObject({
      spectrumHome:'tv', spectrumLayout:'separate',
      spectrumVisible:{kite:true,simulation:false,tv:true,radio:true},
      spectrumOrder:['kite','tv','simulation','radio'],
    });
    expect(localStorage.getItem(EXPERIENCE_KEY)).toContain('"spectrumHome":"tv"');
  });

  it('changes Home when KITE is hidden and restores the choice after reload', () => {
    render(<SpectrumSettings />);
    fireEvent.click(screen.getByRole('checkbox',{name:'Show KITE'}));
    fireEvent.click(screen.getByRole('checkbox',{name:'Show Simulation'}));
    expect(readExperienceOptions()).toMatchObject({
      spectrumHome:'tv',
      spectrumVisible:{kite:false,simulation:false,tv:true,radio:true},
    });
    expect(screen.getByRole('combobox',{name:'Default Spectrum home tab'})).toHaveValue('tv');
    expect(Array.from(screen.getByRole('combobox',{name:'Default Spectrum home tab'}).querySelectorAll('option')).map(e=>e.value))
      .toEqual(['tv','radio']);
    // Migration of previously saved preferences with a now-hidden KITE Home.
    localStorage.setItem(EXPERIENCE_KEY, JSON.stringify({
      ...readExperienceOptions(), spectrumHome:'kite',
    }));
    expect(readExperienceOptions().spectrumHome).toBe('tv');
    // Honor chosen order when Radio comes first.
    expect(normalizeExperienceOptions({
      spectrumHome:'kite',spectrumVisible:{kite:false,simulation:false,tv:true,radio:true},
      spectrumOrder:['radio','tv','kite','simulation'],
    }).spectrumHome).toBe('radio');
  });

  it('allows only Simulation to be selected but never zero destinations', () => {
    render(<SpectrumSettings />);
    fireEvent.click(screen.getByRole('checkbox',{name:'Show KITE'}));
    fireEvent.click(screen.getByRole('checkbox',{name:'Show TV'}));
    fireEvent.click(screen.getByRole('checkbox',{name:'Show Radio'}));
    expect(screen.getByRole('checkbox',{name:'Show Simulation'})).toBeDisabled();
    expect(useExperienceOptions.getState().options.spectrumVisible)
      .toEqual({kite:false,simulation:true,tv:false,radio:false});
  });

  it('migrates old layouts and safely normalizes invalid preferences', () => {
    expect(normalizeExperienceOptions({spectrumLayout:'toggle'}).spectrumLayout).toBe('cycle');
    const invalid = normalizeExperienceOptions({
      spectrumHome:'bogus', spectrumVisible:{kite:false,simulation:false,tv:false,radio:false},
      spectrumOrder:['tv','tv','unknown','kite'], tvLabel:' <script> ',
    });
    expect(invalid.spectrumHome).toBe('kite');
    expect(invalid.spectrumVisible).toEqual({kite:true,simulation:false,tv:false,radio:false});
    expect(invalid.spectrumOrder).toEqual(['tv','kite','simulation','radio']);
    expect(invalid.tvLabel).toBe('script');
    expect(normalizeExperienceOptions({}).spectrumHome).toBe(DEFAULT_EXPERIENCE_OPTIONS.spectrumHome);
  });

  it('sets Radio Home and persists a custom Radio display label', () => {
    render(<SpectrumSettings />);
    fireEvent.change(screen.getByRole('combobox',{name:'Default Spectrum home tab'}),{target:{value:'radio'}});
    fireEvent.change(screen.getByRole('textbox',{name:'Radio display name'}),{target:{value:'My FM'}});
    fireEvent.blur(screen.getByRole('textbox',{name:'Radio display name'}));
    expect(readExperienceOptions()).toMatchObject({spectrumHome:'radio',radioLabel:'My FM'});
  });

  it('keeps unrelated Experience preferences after restoring Spectrum defaults', () => {
    useExperienceOptions.getState().update({
      navHidden:['astro'],tickerLayout:'mobile',spectrumHome:'simulation',
      spectrumLayout:'separate', tvLabel:'Cinema',
    });
    render(<SpectrumSettings />);
    fireEvent.click(screen.getByRole('button',{name:'Restore Spectrum defaults'}));
    const options=useExperienceOptions.getState().options;
    expect(options.spectrumHome).toBe('kite');
    expect(options.spectrumLayout).toBe('cycle');
    expect(options.tickerLayout).toBe('mobile');
    expect(options.navHidden).toEqual(['astro']);
  });
});

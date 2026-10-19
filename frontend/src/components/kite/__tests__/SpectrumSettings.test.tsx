import React from 'react';
import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { SpectrumSettings } from '../SpectrumSettings';
import {
  EXPERIENCE_KEY,
  DEFAULT_EXPERIENCE_OPTIONS,
  normalizeExperienceOptions,
  readExperienceOptions,
  useExperienceOptions,
} from '../../../store/useExperienceOptions';

beforeEach(() => {
  localStorage.clear();
  useExperienceOptions.setState({ options: readExperienceOptions() });
});
afterEach(() => cleanup());

describe('Spectrum settings in Settings → Experience', () => {
  it('starts with the exact KITE⇄Simulation and TV defaults', () => {
    render(<SpectrumSettings />);
    expect(screen.getByRole('combobox', { name:'Spectrum navigation layout' }))
      .toHaveValue('toggle');
    expect(screen.getByRole('checkbox', { name:'Show KITE' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name:'Show Simulation' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name:'Show TV' })).toBeChecked();
    expect(screen.getByRole('textbox', { name:'TV display name' })).toHaveValue('TV');
  });

  it('can customize the layout, TV name, visibility and order independently', () => {
    render(<SpectrumSettings />);
    fireEvent.change(screen.getByRole('combobox',{name:'Spectrum navigation layout'}), {
      target:{value:'separate'},
    });
    fireEvent.change(screen.getByRole('textbox',{name:'TV display name'}),{
      target:{value:'Stream Hub'},
    });
    fireEvent.blur(screen.getByRole('textbox',{name:'TV display name'}));
    fireEvent.click(screen.getByRole('checkbox',{name:'Show Simulation'}));
    fireEvent.click(screen.getByRole('button',{name:'Move TV left'}));
    const value=useExperienceOptions.getState().options;
    expect(value).toMatchObject({
      spectrumLayout:'separate',
      tvLabel:'Stream Hub',
      spectrumVisible:{kite:true,simulation:false,tv:true},
      spectrumOrder:['kite','tv','simulation'],
    });
    expect(readExperienceOptions().tvLabel).toBe('Stream Hub');
    expect(localStorage.getItem(EXPERIENCE_KEY)).toContain('"spectrumLayout":"separate"');
  });

  it('permits TV-only mode with a route to restore settings', () => {
    render(<SpectrumSettings />);
    fireEvent.click(screen.getByRole('checkbox',{name:'Show KITE'}));
    expect(useExperienceOptions.getState().options.spectrumVisible.kite).toBe(false);
    expect(screen.getByRole('checkbox',{name:'Show TV'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button',{name:'Restore Spectrum defaults'}));
    expect(useExperienceOptions.getState().options.spectrumVisible)
      .toEqual({kite:true,simulation:true,tv:true});
  });

  it('keeps one navigable workspace after malformed imported preferences', () => {
    const options=normalizeExperienceOptions({
      spectrumLayout:'unknown',
      spectrumVisible:{kite:false,simulation:false,tv:false},
      spectrumOrder:['invalid','tv','tv','kite'],
      tvLabel:' <script> \u202Ehi\u202C',
    });
    expect(options.spectrumVisible.kite).toBe(true);
    expect(options.spectrumVisible.tv).toBe(false);
    expect(options.spectrumLayout).toBe('toggle');
    expect(options.spectrumOrder).toEqual(['tv','kite','simulation']);
    expect(options.tvLabel).not.toMatch(/[<>\u202e]/);
    expect(normalizeExperienceOptions({}).spectrumVisible)
      .toEqual(DEFAULT_EXPERIENCE_OPTIONS.spectrumVisible);
  });

  it('never destroys other Experience options when Spectrum defaults are restored', () => {
    useExperienceOptions.getState().update({
      tickerLayout:'mobile', navHidden:['astro'],
      spectrumLayout:'separate', tvLabel:'Live Channels',
    });
    render(<SpectrumSettings />);
    fireEvent.click(screen.getByRole('button',{name:'Restore Spectrum defaults'}));
    const opts=useExperienceOptions.getState().options;
    expect(opts.tvLabel).toBe('TV');
    expect(opts.spectrumLayout).toBe('toggle');
    expect(opts.tickerLayout).toBe('mobile');
    expect(opts.navHidden).toEqual(['astro']);
  });

  it('rejects unsafe or empty TV names but preserves arbitrary normal names', () => {
    render(<SpectrumSettings />);
    const textbox=screen.getByRole('textbox',{name:'TV display name'});
    fireEvent.change(textbox,{target:{value:'Movie Night'}});
    fireEvent.blur(textbox);
    expect(useExperienceOptions.getState().options.tvLabel).toBe('Movie Night');
    fireEvent.change(textbox,{target:{value:'   '}});
    fireEvent.blur(textbox);
    expect(useExperienceOptions.getState().options.tvLabel).toBe('TV');
  });
});

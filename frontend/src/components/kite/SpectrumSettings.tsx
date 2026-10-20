import { useEffect, useState } from 'react';
import {
  DEFAULT_EXPERIENCE_OPTIONS,
  SPECTRUM_ORDER,
  useExperienceOptions,
  type SpectrumProduct,
  type SpectrumVisible,
} from '../../store/useExperienceOptions';

const labels: Record<SpectrumProduct, string> = {
  kite: 'KITE', simulation: 'Simulation', tv: 'TV', radio: 'Radio',
};

export function SpectrumSettings() {
  const { options, update } = useExperienceOptions();
  const [nameDraft, setNameDraft] = useState(options.tvLabel);
  useEffect(() => { setNameDraft(options.tvLabel); }, [options.tvLabel]);
  const applyName = () => update({ tvLabel: nameDraft });
  const [radioDraft, setRadioDraft] = useState(options.radioLabel);
  useEffect(() => { setRadioDraft(options.radioLabel); }, [options.radioLabel]);
  const applyRadioName = () => update({ radioLabel: radioDraft });
  const changeVisible = (key: SpectrumProduct, enabled: boolean) => {
    const next: SpectrumVisible = { ...options.spectrumVisible, [key]: enabled };
    if (!SPECTRUM_ORDER.some(product => next[product])) return;
    // Disabling the current Home must also move startup/Home to a destination
    // that is still selected, using the operator's Spectrum order.
    const spectrumHome = next[options.spectrumHome]
      ? options.spectrumHome
      : options.spectrumOrder.find(product => next[product]) ?? 'kite';
    update({ spectrumVisible: next, spectrumHome });
  };
  const move = (key: SpectrumProduct, step: number) => {
    const order = [...options.spectrumOrder];
    const current = order.indexOf(key);
    const next = current + step;
    if (current < 0 || next < 0 || next >= order.length) return;
    [order[current], order[next]] = [order[next], order[current]];
    update({ spectrumOrder: order });
  };
  return <section className="appearance-settings" aria-label="Spectrum navigation settings">
    <h3>Sterling Spectrum</h3>
    <p>Set the landing workspace, Home destination and how Spectrum navigation appears. These settings never change trading permissions or execution.</p>
    <label style={{ display:'block', margin:'14px 0' }}>
      Default tab and Sterling Home
      <select aria-label="Default Spectrum home tab" value={options.spectrumHome}
        onChange={event => update({ spectrumHome: event.target.value as SpectrumProduct })}>
        {options.spectrumOrder.filter(key => options.spectrumVisible[key]).map(key =>
          <option key={key} value={key}>{key === 'tv' ? options.tvLabel : key === 'radio' ? options.radioLabel : labels[key]}</option>)}
      </select>
    </label>
    <label style={{ display:'block', margin:'14px 0' }}>
      Navigation layout
      <select aria-label="Spectrum navigation layout" value={options.spectrumLayout}
        onChange={event => update({ spectrumLayout: event.target.value === 'separate' ? 'separate' : 'cycle' })}>
        <option value="cycle">Toggle — one button, cycle through selected destinations</option>
        <option value="separate">Buttons — show selected destinations side by side</option>
      </select>
    </label>
    <fieldset style={{ border:'1px solid var(--k-border)', padding:12, borderRadius:8 }}>
      <legend style={{ padding:'0 6px' }}>Destinations in toggle / buttons</legend>
      {options.spectrumOrder.map((key, index) => {
        const cannotHide = options.spectrumVisible[key] &&
          options.spectrumOrder.filter(product => options.spectrumVisible[product]).length === 1;
        return <div key={key} style={{
          display:'flex', alignItems:'center', justifyContent:'space-between', gap:12,
          padding:'6px 0', borderBottom:'1px solid var(--k-border)',
        }}>
          <label style={{ display:'flex', alignItems:'center', gap:8 }}>
            <input type="checkbox" aria-label={`Show ${labels[key]}`}
              checked={options.spectrumVisible[key]} disabled={cannotHide}
              onChange={e => changeVisible(key,e.target.checked)} />
            {labels[key]}
          </label>
          <div style={{ display:'flex', gap:5 }}>
            <button type="button" aria-label={`Move ${labels[key]} left`}
              onClick={() => move(key,-1)} disabled={index===0}>←</button>
            <button type="button" aria-label={`Move ${labels[key]} right`}
              onClick={() => move(key,1)} disabled={index===options.spectrumOrder.length-1}>→</button>
          </div>
        </div>;
      })}
    </fieldset>
    <p style={{ marginTop:7, fontSize:12, color:'var(--k-dim)' }}>
      At least one destination must be selected. Home and startup use a selected destination; if Home is hidden, the first enabled destination in your order becomes Home. Toggle follows this order. Simulation still requires owner authorization.
    </p>
    <label style={{ display:'block', margin:'14px 0' }}>
      TV display name
      <input aria-label="TV display name" maxLength={24} type="text"
        placeholder="TV" value={nameDraft}
        onChange={e => setNameDraft(e.target.value)}
        onBlur={applyName}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); applyName(); e.currentTarget.blur(); } }} />
      <small style={{ display:'block' }}>Up to 24 characters. Saved in this browser only; IPTV remains isolated from trading.</small>
    </label>
    <label style={{ display:'block', margin:'14px 0' }}>
      Radio display name
      <input aria-label="Radio display name" maxLength={24} type="text" placeholder="Radio"
        value={radioDraft} onChange={e => setRadioDraft(e.target.value)} onBlur={applyRadioName}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); applyRadioName(); e.currentTarget.blur(); } }} />
      <small style={{ display:'block' }}>Radio is isolated from trading and uses its own player.</small>
    </label>
    <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
      <button type="button" onClick={() => { setNameDraft('TV'); update({tvLabel:'TV'}); }}>Reset TV name</button>
      <button type="button" onClick={() => { setRadioDraft('Radio'); update({radioLabel:'Radio'}); }}>Reset Radio name</button>
      <button type="button" onClick={() => update({
        spectrumLayout:DEFAULT_EXPERIENCE_OPTIONS.spectrumLayout,
        spectrumVisible:{...DEFAULT_EXPERIENCE_OPTIONS.spectrumVisible},
        spectrumOrder:[...SPECTRUM_ORDER],
        spectrumHome:'kite',
        tvLabel:'TV',
        radioLabel:'Radio',
      })}>Restore Spectrum defaults</button>
    </div>
  </section>;
}

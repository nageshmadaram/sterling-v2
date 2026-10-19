import { useEffect, useState } from 'react';
import {
  DEFAULT_EXPERIENCE_OPTIONS,
  SPECTRUM_ORDER,
  useExperienceOptions,
  type SpectrumProduct,
  type SpectrumVisible,
} from '../../store/useExperienceOptions';

const labels: Record<SpectrumProduct, string> = {
  kite: 'KITE', simulation: 'Simulation', tv: 'TV',
};

export function SpectrumSettings() {
  const { options, update } = useExperienceOptions();
  const [nameDraft, setNameDraft] = useState(options.tvLabel);
  useEffect(() => { setNameDraft(options.tvLabel); }, [options.tvLabel]);
  const applyName = () => update({ tvLabel: nameDraft });
  const changeVisible = (key: SpectrumProduct, enabled: boolean) => {
    const next: SpectrumVisible = { ...options.spectrumVisible, [key]: enabled };
    if (!next.kite && !next.tv) return;
    update({ spectrumVisible: next });
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
    <p>Choose how KITE, Simulation and TV appear beside the Sterling logo. These settings change navigation only, not trading permissions or execution.</p>
    <label style={{ display:'block', margin:'14px 0' }}>
      Navigation layout
      <select aria-label="Spectrum navigation layout"
        value={options.spectrumLayout}
        onChange={event => update({
          spectrumLayout: event.target.value === 'separate' ? 'separate'
            : event.target.value === 'cycle' ? 'cycle' : 'toggle',
        })}>
        <option value="toggle">KITE ⇄ Simulation toggle + TV (default)</option>
        <option value="separate">KITE, Simulation and TV as separate tabs</option>
        <option value="cycle">One button: KITE → Simulation → TV → KITE</option>
      </select>
    </label>
    <fieldset style={{ border:'1px solid var(--k-border)', padding:12, borderRadius:8 }}>
      <legend style={{ padding:'0 6px' }}>Visible destinations</legend>
      {options.spectrumOrder.map((key, index) => {
        const lastMain = key === 'kite' && !options.spectrumVisible.tv ||
          key === 'tv' && !options.spectrumVisible.kite;
        const cannotHide = options.spectrumVisible[key] && lastMain;
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
      In toggle mode, KITE and Simulation share the original KITE switch. Separate mode gives each visible destination its own tab. Cycle mode uses one button to visit visible destinations in the order above. Hiding Simulation does not remove the return route from an open Simulation session. At least KITE or TV must remain visible.
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
    <div style={{ display:'flex', gap:8, alignItems:'center', flexWrap:'wrap' }}>
      <button type="button" onClick={() => { setNameDraft('TV'); update({tvLabel:'TV'}); }}>Reset TV name</button>
      <button type="button" onClick={() => update({
        spectrumLayout:DEFAULT_EXPERIENCE_OPTIONS.spectrumLayout,
        spectrumVisible:{...DEFAULT_EXPERIENCE_OPTIONS.spectrumVisible},
        spectrumOrder:[...SPECTRUM_ORDER],
        tvLabel:'TV',
      })}>Restore Spectrum defaults</button>
    </div>
  </section>;
}

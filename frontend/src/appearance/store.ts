import { create } from 'zustand';
import { FONTS, THEME_PRESETS, type FontId } from './catalog';
import { THEME_TOKENS } from '../styles/theme';
export const APPEARANCE_KEY = 'sterling_appearance_v1';
export type Appearance = { base: 'light' | 'dark' | 'grey'; preset: string; font: FontId | 'preset'; motion: 'system' | 'off' | 'calm' | 'expressive'; background: 'plain' | 'glow' | 'grid' | 'dots'; corners: 'preset' | 'square' | 'soft'; accent: string; favorites: string[] };
export const DEFAULT_APPEARANCE: Appearance = { base: 'light', preset: '', font: 'preset', motion: 'system', background: 'plain', corners: 'preset', accent: '', favorites: [] };
export function validateAppearance(raw: unknown): Appearance {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Expected an appearance settings object.');
  const r = raw as Record<string, unknown>;
  const one = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
    if (r[key] == null) return fallback;
    if (!allowed.includes(r[key] as T)) throw new Error(`Invalid ${key}.`);
    return r[key] as T;
  };
  const preset = one('preset', ['', ...THEME_PRESETS.map(p => p.id)], '');
  const accent = typeof r.accent === 'string' ? r.accent : '';
  if (accent && !/^#[0-9a-f]{6}$/i.test(accent)) throw new Error('Accent must be a six-digit hex colour.');
  return { base: one('base', ['light', 'dark', 'grey'], 'light'), preset, accent, font: one('font', ['preset', ...Object.keys(FONTS)] as Array<FontId | 'preset'>, 'preset'),
    motion: one('motion', ['system', 'off', 'calm', 'expressive'], 'system'),
    background: one('background', ['plain', 'glow', 'grid', 'dots'], 'plain'),
    corners: one('corners', ['preset', 'square', 'soft'], 'preset'),
    favorites: Array.isArray(r.favorites) ? [...new Set(r.favorites.filter((id): id is string => typeof id === 'string' && THEME_PRESETS.some(p => p.id === id)))] : [] };
}
function load(): Appearance { try { const saved = localStorage.getItem(APPEARANCE_KEY); return saved ? validateAppearance(JSON.parse(saved)) : { ...DEFAULT_APPEARANCE }; } catch { return { ...DEFAULT_APPEARANCE }; } }
export const useAppearance = create<{ settings: Appearance; update: (patch: Partial<Appearance>) => void; replace: (settings: Appearance) => void }>((set, get) => ({
  settings: load(),
  update: patch => get().replace(validateAppearance({ ...get().settings, ...patch })),
  replace: settings => { const valid = validateAppearance(settings); try { localStorage.setItem(APPEARANCE_KEY, JSON.stringify(valid)); } catch { /* session still works */ } set({ settings: valid }); },
}));
function blend(a: string, b: string, ratio: number): string {
  const parse = (s: string) => s.slice(1).match(/.{2}/g)!.map(v => parseInt(v, 16));
  return '#' + parse(a).map((v, i) => Math.round(v * (1 - ratio) + parse(b)[i] * ratio).toString(16).padStart(2, '0')).join('');
}
/** Applies only visual tokens. Trading semantics and execution state are untouched. */
export function applyAppearance(settings: Appearance, base: string): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  // Remove our previous overrides so switching back to stock themes fully restores them.
  for (const [name] of THEME_TOKENS) root.style.removeProperty(`--k-${name}`);
  for (const name of ['bg','bg-card','bg-surface','bg-input','border','border-light','text-primary','text-muted','text-dim','text-faint','brand','brand-1','brand-2','brand-3','brand-grad','blue','app-font','k-font','appearance-radius']) root.style.removeProperty(`--${name}`);
  const selected = THEME_PRESETS.find(p => p.id === settings.preset);
  const p = selected && (selected.dark ? base !== 'light' : base === 'light') ? selected : undefined;
  root.dataset.appearance = p?.id ?? 'default';
  root.dataset.appearanceMotion = settings.motion;
  root.dataset.appearanceBackground = settings.background;
  const put = (key: string, value: string) => root.style.setProperty(`--${key}`, value);
  if (p) {
    for (const [name] of THEME_TOKENS) {
      if (name === 'bg') put('k-bg', p.surface);
      else if (name.startsWith('surface') || name.startsWith('hairline')) put(`k-${name}`, blend(p.surface, p.bg, name.includes('hover') ? .8 : .5));
      else if (name.startsWith('border')) put(`k-${name}`, blend(p.surface, p.text, .22));
      else if (/^(ink|text|dim|faint)/.test(name)) put(`k-${name}`, blend(p.text, p.surface, name.includes('faint') ? .32 : name.includes('dim') ? .24 : .1));
    }
    put('bg', p.bg); put('bg-card', p.surface); put('bg-surface', blend(p.surface,p.bg,.5)); put('bg-input',p.surface);
    put('border',blend(p.surface,p.text,.22)); put('border-light',blend(p.surface,p.text,.3));
    put('text-primary',p.text); put('text-muted',blend(p.text,p.surface,.15)); put('text-dim',blend(p.text,p.surface,.24)); put('text-faint',blend(p.text,p.surface,.32));
  }
  const accent = settings.accent || p?.accent;
  if (accent) { put('brand-1',accent); put('brand-2',accent); put('brand-3',accent); put('brand-grad',`linear-gradient(95deg, ${accent}, ${accent})`); for (const name of ['k-brand','k-brand-deep','brand','k-blue','k-blue-kite','blue']) put(name, accent); }
  const font = settings.font === 'preset' ? p?.font ?? 'inter' : settings.font;
  put('app-font',FONTS[font]); put('k-font',FONTS[font]);
  put('appearance-radius', `${settings.corners === 'square' ? 0 : settings.corners === 'soft' ? 18 : p?.radius ?? 9}px`);
  root.dispatchEvent(new Event('sterling-appearance-change'));
}

import { create } from 'zustand';

export const EXPERIENCE_KEY = 'sterling.experience.options.v1';
export const NAV_OPTIONS = [
  ['dashboard','Dashboard'], ['astro','Astrology'], ['pcr','PCR'],
  ['orders','Orders'], ['holdings','Holdings'], ['positions','Positions'],
  ['journal','Journal'], ['more','More'], ['data','Data'],
  ['backtest','Backtest'], ['connect','Connect'], ['help','Help'],
] as const;
export type TickerContent = 'graph'|'pcr'|'both'|'badge'|'detail'|'price';
export type SpectrumProduct = 'kite'|'simulation'|'tv'|'radio';
export type SpectrumLayout = 'cycle'|'separate';
export type SpectrumHome = SpectrumProduct;
export type SpectrumVisible = Record<SpectrumProduct, boolean>;
export const SPECTRUM_ORDER: readonly SpectrumProduct[] = ['kite','simulation','tv','radio'];
export type ExperienceOptions = {
  navOrder: string[];
  navHidden: string[];
  tickerLayout: 'classic'|'mobile';
  tickerContent: TickerContent;
  pcrMetric: 'oi'|'volume'|'changeOi';
  pcrTimestamp: boolean;
  pcrRatio: boolean;
  /** cycle = one toggle through enabled destinations; separate = side-by-side buttons. */
  spectrumLayout: SpectrumLayout;
  spectrumVisible: SpectrumVisible;
  spectrumOrder: SpectrumProduct[];
  /** Target used on startup and for either Home control. */
  spectrumHome: SpectrumHome;
  tvLabel: string;
  radioLabel: string;
};

export const DEFAULT_EXPERIENCE_OPTIONS: ExperienceOptions = {
  navOrder: NAV_OPTIONS.map(n => n[0]), navHidden: [],
  tickerLayout: 'classic', tickerContent: 'graph',
  pcrMetric: 'oi', pcrTimestamp: true, pcrRatio: true,
  spectrumLayout: 'cycle',
  spectrumVisible: { kite: true, simulation: true, tv: true, radio: true },
  spectrumOrder: [...SPECTRUM_ORDER],
  spectrumHome: 'kite',
  tvLabel: 'TV',
  radioLabel: 'Radio',
};
const has = (value: unknown, values: readonly string[]): value is string =>
  typeof value === 'string' && values.includes(value);
const cleanTvLabel = (input: unknown): string => {
  if (typeof input !== 'string') return 'TV';
  // No markup/control/bidi override or invisible-only labels in navigation.
  const label = input.replace(/[<>\\\"'\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/\s+/g, ' ').trim().slice(0, 24).trim();
  return label || 'TV';
};

export function normalizeExperienceOptions(input: unknown): ExperienceOptions {
  const raw: Record<string, unknown> = input && typeof input === 'object' && !Array.isArray(input)
    ? input as Record<string, unknown> : {};
  const ids = NAV_OPTIONS.map(n => n[0]) as string[];
  const navOrder: string[] = [
    ...new Set<string>([
      ...(Array.isArray(raw.navOrder)
        ? raw.navOrder.filter((x: unknown): x is string => has(x, ids)) : []),
      ...ids,
    ]),
  ];
  const navHidden: string[] = Array.isArray(raw.navHidden)
    ? [...new Set<string>(raw.navHidden.filter((x: unknown): x is string =>
      has(x, ids) && !['connect','help'].includes(x)))]
    : [];
  const selected = raw.spectrumVisible && typeof raw.spectrumVisible === 'object'
    ? raw.spectrumVisible as Record<string, unknown> : {};
  const spectrumVisible: SpectrumVisible = {
    kite: selected.kite !== false,
    simulation: selected.simulation !== false,
    tv: selected.tv !== false,
    radio: selected.radio !== false,
  };
  // Keep at least one selected destination. Home independently restores KITE
  // even when its Spectrum navigation button is hidden.
  if (!SPECTRUM_ORDER.some(key => spectrumVisible[key])) spectrumVisible.kite = true;
  const order: SpectrumProduct[] = [
    ...new Set<SpectrumProduct>([
      ...(Array.isArray(raw.spectrumOrder)
        ? raw.spectrumOrder.filter((x: unknown): x is SpectrumProduct =>
          has(x, SPECTRUM_ORDER)) : []),
      ...SPECTRUM_ORDER,
    ]),
  ];
  return {
    navOrder, navHidden,
    tickerLayout: raw.tickerLayout === 'mobile' ? 'mobile' : 'classic',
    tickerContent: has(raw.tickerContent, ['graph','pcr','both','badge','detail','price'])
      ? raw.tickerContent as TickerContent : 'graph',
    pcrMetric: has(raw.pcrMetric, ['oi','volume','changeOi'])
      ? raw.pcrMetric as ExperienceOptions['pcrMetric'] : 'oi',
    pcrTimestamp: raw.pcrTimestamp !== false,
    pcrRatio: raw.pcrRatio !== false,
    spectrumLayout: raw.spectrumLayout === 'separate' ? 'separate' : 'cycle',
    spectrumVisible, spectrumOrder: order,
    spectrumHome: has(raw.spectrumHome, SPECTRUM_ORDER) ? raw.spectrumHome as SpectrumHome : 'kite',
    tvLabel: cleanTvLabel(raw.tvLabel),
    radioLabel: raw.radioLabel == null ? 'Radio' : cleanTvLabel(raw.radioLabel).replace(/^TV$/, 'Radio'),
  };
}

export function readExperienceOptions(): ExperienceOptions {
  try {
    return normalizeExperienceOptions(JSON.parse(localStorage.getItem(EXPERIENCE_KEY) ?? '{}'));
  } catch {
    return normalizeExperienceOptions({});
  }
}
export const useExperienceOptions = create<{
  options: ExperienceOptions;
  update: (patch: Partial<ExperienceOptions>) => void;
}>(set => ({
  options: readExperienceOptions(),
  update: patch => set(state => {
    const options = normalizeExperienceOptions({ ...state.options, ...patch });
    try { localStorage.setItem(EXPERIENCE_KEY, JSON.stringify(options)); }
    catch { /* settings still apply this session if browser storage is blocked */ }
    return { options };
  }),
}));
export function orderedNavigation<T extends {id:string}>(items:T[],options:ExperienceOptions):T[] {
  return [...items].filter(i => !options.navHidden.includes(i.id))
    .sort((a,b) => options.navOrder.indexOf(a.id)-options.navOrder.indexOf(b.id));
}

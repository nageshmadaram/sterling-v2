import { create } from 'zustand';
export const MOBILE_STYLE_KEY = 'sterling_mobile_style';
export type MobileStyle = 'app' | 'full';
function savedStyle(): MobileStyle {
  try { return localStorage.getItem(MOBILE_STYLE_KEY) === 'full' ? 'full' : 'app'; } catch { return 'app'; }
}
export const useMobileExperience = create<{style:MobileStyle; setStyle:(style:MobileStyle)=>void}>(set => ({
  style:savedStyle(),
  setStyle:style => { try { localStorage.setItem(MOBILE_STYLE_KEY,style); } catch { /* preference remains usable in memory */ } set({style}); },
}));

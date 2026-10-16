import { create } from 'zustand';

interface KiteStatusCardStore {
  isOpen: boolean;
  loginAccountId: string | null;
  loginRequested: boolean;
  requestLogin: (accountId?: string) => void;
  openCard: () => void;
  closeCard: () => void;
  toggleCard: () => void;
}

export const useKiteStatusCardStore = create<KiteStatusCardStore>((set) => ({
  isOpen: false,
  loginAccountId: null,
  loginRequested: false,
  requestLogin: (accountId) => set({ isOpen: true, loginAccountId: accountId || null, loginRequested: true }),
  openCard: () => set({ isOpen: true }),
  closeCard: () => set({ isOpen: false, loginAccountId: null, loginRequested: false }),
  toggleCard: () => set((state) => ({ isOpen: !state.isOpen, loginAccountId: null, loginRequested: false })),
}));

export const openKiteStatusCard = () => useKiteStatusCardStore.getState().openCard();
export const closeKiteStatusCard = () => useKiteStatusCardStore.getState().closeCard();
export const toggleKiteStatusCard = () => useKiteStatusCardStore.getState().toggleCard();

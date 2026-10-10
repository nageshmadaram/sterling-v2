import { create } from 'zustand';

/**
 * Which book the app is showing: the real Zerodha account (`live`) or the owner's virtual
 * tenant (`simulation`). The choice is remembered per browser, and everything that talks to the
 * backend reads it at the moment it sends: `utils/api.ts` adds the `X-Sterling-View` header, the
 * stream sockets add `?view=simulation`, and `ViewQueryProvider` keeps one query cache per view
 * so rows from one view can never be painted in the other.
 */
export type AppView = 'live' | 'simulation';

export const VIEW_STORAGE_KEY = 'sterling.view';
/** Request header the backend reads (`app.core.auth.VIEW_HEADER`). */
export const VIEW_HEADER = 'X-Sterling-View';
export const VIEW_QUERY = 'view';

// Only the exact string "simulation" opts in; a damaged value falls back to the real book.
function readStored(): AppView {
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === 'simulation' ? 'simulation' : 'live';
  } catch {
    return 'live';
  }
}

function writeStored(view: AppView): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch { /* private window or blocked storage: the view still works for this page load */ }
}

interface ViewState {
  view: AppView;
  setView: (view: AppView) => void;
}

export const useViewStore = create<ViewState>((set, get) => ({
  view: readStored(),
  setView: (view) => {
    if (get().view === view) return;
    writeStored(view);
    set({ view });
  },
}));

/** Non-React read, for the API layer and the socket modules. */
export function getView(): AppView {
  return useViewStore.getState().view;
}

export const useView = (): AppView => useViewStore((s) => s.view);
export const useIsSimulation = (): boolean => useViewStore((s) => s.view === 'simulation');

/** EventSource and WebSocket cannot carry a header, so the view rides in the URL. */
export function withViewQuery(url: string, view: AppView = getView()): string {
  if (view !== 'simulation') return url;
  return `${url}${url.includes('?') ? '&' : '?'}${VIEW_QUERY}=simulation`;
}

import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Dashboard } from './pages/Dashboard';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useTheme } from './store/useStore';
import { useViewportScale } from './hooks/useViewportScale';
import { LoginGate } from './components/auth/LoginGate';
import { LiveFeedBadge } from './components/live/LiveFeedBadge';
import { useLiveResync } from './hooks/useLiveResync';
import { appQueryDefaults } from './utils/queryDefaults';
import { ViewQueryProvider } from './components/sim/ViewQueryProvider';

// Holds the sign-in gate only. Everything the operator trades or reads lives under
// ViewQueryProvider, which gives the LIVE and SIMULATION views a cache each.
const queryClient = new QueryClient({ defaultOptions: appQueryDefaults });

/** The signed-in app. Remounts, with an empty cache, whenever the view switches. */
function ViewShell() {
  // Refetch trading state after reconnects, restarts, wake-ups and writes.
  useLiveResync();
  return (
    <>
      <Dashboard />
      <LiveFeedBadge />
    </>
  );
}

function ThemedApp() {
  const theme = useTheme();
  // Keeps the layout on its design width whatever the monitor hands us.
  useViewportScale();

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme === 'light' ? 'light' : 'dark';
  }, [theme]);

  // Single root view. Dashboard internally toggles between basic/pro layouts
  // via the `appMode` Zustand selector (see useStore.useAppMode).
  return (
    <LoginGate>
      <ViewQueryProvider>
        <ViewShell />
      </ViewQueryProvider>
    </LoginGate>
  );
}

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemedApp />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

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

const queryClient = new QueryClient({ defaultOptions: appQueryDefaults });

function ThemedApp() {
  const theme = useTheme();
  // Keeps the layout on its design width whatever the monitor hands us.
  useViewportScale();
  // Refetch trading state after reconnects, restarts, wake-ups and writes.
  useLiveResync();

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme === 'light' ? 'light' : 'dark';
  }, [theme]);

  // Single root view. Dashboard internally toggles between basic/pro layouts
  // via the `appMode` Zustand selector (see useStore.useAppMode).
  return (
    <LoginGate>
      <Dashboard />
      <LiveFeedBadge />
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

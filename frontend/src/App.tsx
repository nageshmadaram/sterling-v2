import { lazy, Suspense, useEffect } from 'react';
import { QueryClient, QueryClientProvider, keepPreviousData } from '@tanstack/react-query';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useTheme } from './store/useStore';
import { useViewportScale } from './hooks/useViewportScale';
import { LoginGate } from './components/auth/LoginGate';

const Dashboard = lazy(() => import('./pages/Dashboard').then((module) => ({ default: module.Dashboard })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error: any) => {
        const status = error?.status || error?.response?.status;
        if (status === 401 || status === 403 || status === 409) return false;
        return failureCount < 1;
      },
      staleTime: 10_000,
      placeholderData: keepPreviousData,
      refetchOnWindowFocus: false,
    },
  },
});

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
      <Suspense fallback={<div role="status">Loading Sterling…</div>}>
        <Dashboard />
      </Suspense>
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

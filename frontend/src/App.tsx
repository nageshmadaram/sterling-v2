import { useAppearance, applyAppearance } from './appearance/store';
import { useLayoutEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useTheme } from './store/useStore';
import { useViewportScale } from './hooks/useViewportScale';
import { LoginGate } from './components/auth/LoginGate';
import { appQueryDefaults } from './utils/queryDefaults';
import { ConfirmProvider } from './components/kite/ConfirmDialog';
import { WorkspaceRouter } from './pages/WorkspaceRouter';
const queryClient = new QueryClient({ defaultOptions: appQueryDefaults });

function ThemedApp() {
  const theme = useTheme();
  const appearance = useAppearance(s => s.settings);
  // Keeps the layout on its design width whatever the monitor hands us.
  useViewportScale();

  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme === 'light' ? 'light' : 'dark';
    applyAppearance(appearance, theme);
  }, [theme, appearance]);

  return <LoginGate><WorkspaceRouter /></LoginGate>;
}

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ConfirmProvider>
          <ThemedApp />
        </ConfirmProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

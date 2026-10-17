import LoginGate from '../src/components/LoginGate';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useTheme } from '../src/store/useStore';
import { theme } from '../src/styles/theme';

export default function RootLayout() {
  const currentTheme = useTheme();

  return (
    <>
      <StatusBar style="light" />
      <LoginGate>
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: theme.colors.background },
        }}
      >
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      </Stack>
      </LoginGate>
    </>
  );
}

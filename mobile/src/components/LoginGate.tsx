import React, { useRef, useState } from 'react';
import { ActivityIndicator, Button, KeyboardAvoidingView, Platform, Text, TextInput, View } from 'react-native';
import { useStore } from '../store/useStore';
import { theme } from '../styles/theme';

export default function LoginGate({ children }: { children: React.ReactNode }) {
  const token = useStore(s => s.authToken);
  const host = useStore(s => s.apiHost);
  const [server, setServer] = useState(host);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const login = async () => {
    if (pending.current) return;
    const origin = server.trim().replace(/\/+$/, '');
    try {
      const url = new URL(origin);
      const local = ['localhost', '127.0.0.1', '10.0.2.2', '[::1]'].includes(url.hostname);
      if ((url.protocol !== 'https:' && !(local && url.protocol === 'http:')) ||
          url.username || url.password || url.search || url.hash || url.pathname !== '/') {
        throw new Error();
      }
    } catch {
      setError('Enter your Sterling HTTPS server address. Local emulator addresses may use HTTP.');
      return;
    }
    if (!username.trim() || !password) { setError('Enter your username and password.'); return; }
    pending.current = true;
    setBusy(true);
    setError('');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(`${origin}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ username: username.trim(), password }).toString(),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(response.status === 401 ? 'Username or password was not accepted.' : 'Sign-in is unavailable. Try again shortly.');
      const session = await response.json();
      if (typeof session.access_token !== 'string' || !session.access_token) throw new Error('Sign-in returned an invalid session.');
      useStore.getState().setApiHost(origin);
      useStore.getState().setSession(session.access_token, session.refresh_token || '');
      setPassword('');
    } catch (e) {
      setError(e instanceof Error && e.name !== 'AbortError' ? e.message : 'Connection timed out. Check the server address.');
    } finally {
      clearTimeout(timer);
      pending.current = false;
      setBusy(false);
    }
  };
  if (token) return <>{children}</>;
  const input = { color: theme.colors.textBright, borderColor: theme.colors.border, borderWidth: 1, padding: 12, marginVertical: 8, borderRadius: 6 };
  return <KeyboardAvoidingView style={{ flex: 1, justifyContent: 'center', backgroundColor: theme.colors.background, padding: 24 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <View>
      <Text style={{ color: theme.colors.textBright, fontSize: 24, marginBottom: 20 }}>Sign in to Sterling</Text>
      <TextInput accessibilityLabel="Server address" placeholder="https://your-sterling-server" placeholderTextColor={theme.colors.textMuted} style={input} value={server} onChangeText={setServer} autoCapitalize="none" keyboardType="url" editable={!busy} />
      <TextInput accessibilityLabel="Username" placeholder="Username" placeholderTextColor={theme.colors.textMuted} style={input} value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} editable={!busy} />
      <TextInput accessibilityLabel="Password" placeholder="Password" placeholderTextColor={theme.colors.textMuted} style={input} value={password} onChangeText={setPassword} secureTextEntry editable={!busy} onSubmitEditing={login} />
      {error ? <Text accessibilityRole="alert" style={{ color: theme.colors.red, marginVertical: 12 }}>{error}</Text> : null}
      {busy ? <ActivityIndicator /> : <Button title="Sign in" onPress={login} />}
    </View>
  </KeyboardAvoidingView>;
}

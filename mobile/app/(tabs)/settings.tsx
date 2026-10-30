import React, { useState, useEffect } from 'react';
import { 
  View, Text, StyleSheet, ScrollView, Switch, 
  TextInput, TouchableOpacity, Alert, ActivityIndicator 
} from 'react-native';
import Header from '../../src/components/Header';
import { api } from '../../src/services/api';
import { assertGlobalRiskControlAllowed, canManageGlobalRisk, currentWorkspacePolicy } from '../../src/services/workspacePolicy';
import { theme } from '../../src/styles/theme';
import { useStore } from '../../src/store/useStore';


export default function SettingsDashboard() {
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  
  // States
  const [killSwitch, setKillSwitch] = useState({ enabled: false, reason: '' });
  const [killKnown, setKillKnown] = useState(false);
  const [lossKnown, setLossKnown] = useState(false);
  const [controlAllowed, setControlAllowed] = useState(false);
  const [loadError, setLoadError] = useState('');
  // INR, and stored negative. The USD fields these used to carry went with the
  // crypto surface, along with every route this screen called.
  const [dailyLoss, setDailyLoss] = useState({ enabled: true, soft_warn_inr: 1000, hard_halt_inr: 1500 });
  const [telegram, setTelegram] = useState({ chat_id: '', reachable: false, hint: '' });
  const [systemInfo, setSystemInfo] = useState<any>(null);

  const fetchSettings = async () => {
    try {
      const policy = await currentWorkspacePolicy();
      setControlAllowed(canManageGlobalRisk(policy));
      // allSettled, not all. One dead route used to reject the whole batch, so a
      // single 404 left the screen blank -- including the kill switch, which is
      // the control you most need when something else is already broken.
      const [ks, dl, tg] = await Promise.allSettled([
        api.get<any>('/api/v1/kite/trading/kill-switch'),
        api.get<any>('/api/v1/kite/risk/daily-loss'),
        api.get<any>('/api/v1/kite/telegram'),
      ]);

      setKillKnown(ks.status === 'fulfilled' && typeof ks.value.enabled === 'boolean');
      setLossKnown(dl.status === 'fulfilled');
      setLoadError(ks.status === 'rejected' || dl.status === 'rejected' ? 'Trading safety settings could not be verified. Refresh before changing them.' : '');
      if (ks.status === 'fulfilled') {
        setKillSwitch({ enabled: ks.value.enabled, reason: ks.value.reason || '' });
      }
      if (dl.status === 'fulfilled') {
        // Shown as a positive rupee figure; the backend stores a loss.
        setDailyLoss({
          enabled: dl.value.enabled !== false,
          soft_warn_inr: Math.abs(dl.value.soft_warn_inr ?? 1000),
          hard_halt_inr: Math.abs(dl.value.hard_halt_inr ?? 1500),
        });
      }
      if (tg.status === 'fulfilled') {
        // Endpoint returns { targets: [...] } (TargetListResponse)
        const targets = (tg.value as any)?.targets;
        const first = Array.isArray(targets) ? targets[0] : undefined;
        setTelegram({
          chat_id: first?.chat_id || '',
          reachable: first?.reachable || false,
          hint: first?.bot_token_hint || '',
        });
      }
      setSystemInfo(null);
    } catch (e) {
      setControlAllowed(false);
      setKillKnown(false);
      setLossKnown(false);
      setLoadError('Workspace policy could not be verified. Controls are unavailable.');
      console.warn('Error loading settings:', e);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchSettings().finally(() => setLoading(false));
  }, []);

  const handleToggleKill = async () => {
    if (!killKnown || !controlAllowed || busy) return;
    setBusy(true);
    try {
      await assertGlobalRiskControlAllowed();
      const next = !killSwitch.enabled;
      await api.post('/api/v1/kite/trading/kill-switch', {
        enabled: next,
        reason: next ? 'manual mobile operator halt' : ''
      });
      Alert.alert(next ? 'New entries halted' : 'Entry halt removed', 'Existing positions remain managed. Other entry checks still apply.');
      await fetchSettings();
    } catch (e: any) {
      Alert.alert('Error', e.message || 'Failed to update kill switch.');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveDailyLoss = async () => {
    if (!lossKnown || !controlAllowed || busy) return;
    const soft = Math.abs(Number(dailyLoss.soft_warn_inr));
    const hard = Math.abs(Number(dailyLoss.hard_halt_inr));
    if (!Number.isFinite(soft) || !Number.isFinite(hard) || (dailyLoss.enabled && (hard <= 0 || soft > hard))) {
      Alert.alert('Invalid loss limits', 'Use finite rupee values. The halt limit must be positive and at least the warning limit.');
      return;
    }
    setBusy(true);
    try {
      await assertGlobalRiskControlAllowed();
      await api.put('/api/v1/kite/risk/daily-loss', {
        enabled: dailyLoss.enabled,
        // Typed as a positive rupee amount, stored as the loss it describes.
        soft_warn_inr: -Math.abs(Number(dailyLoss.soft_warn_inr)),
        hard_halt_inr: -Math.abs(Number(dailyLoss.hard_halt_inr)),
      });
      Alert.alert('Success', 'Daily loss limits updated successfully.');
      await fetchSettings();
    } catch (e: any) {
      Alert.alert('Error', e.message || 'Failed to save daily loss configurations.');
    } finally {
      setBusy(false);
    }
  };

  // Telegram targets (bots + chat IDs) are managed on the desktop app under
  // Connect > Notifications. The mobile screen shows read-only status only.
  const handleSaveTelegram = () => {
    Alert.alert(
      'Manage on desktop',
      'Telegram alert bots are added and configured from the desktop app under Connect → Notifications.',
    );
  };

  const handleTestTelegram = () => {
    Alert.alert(
      'Manage on desktop',
      'Send a test message from the desktop app under Connect → Notifications.',
    );
  };

  if (loading) {
    return (
      <View style={[styles.container, styles.center]}>
        <ActivityIndicator size="large" color={theme.colors.blue} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Header />
      <ScrollView contentContainerStyle={styles.scrollContainer}>
        
        {loadError ? <View style={styles.card}><Text accessibilityRole="alert" style={{ color: theme.colors.red }}>{loadError}</Text><TouchableOpacity onPress={fetchSettings}><Text style={{ color: theme.colors.blue, paddingVertical: 12 }}>Refresh safety settings</Text></TouchableOpacity></View> : null}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>BROKER ACCOUNT</Text>
          <Text style={styles.cardSubtitle}>Orders use your connected Kite account. Check account readiness and entry safeguards before trading.</Text>
          <TouchableOpacity onPress={() => useStore.getState().setSession('', '')}>
            <Text style={{ color: theme.colors.blue, paddingVertical: 12 }}>Sign out</Text>
          </TouchableOpacity>
        </View>

        {!controlAllowed ? <Text accessibilityRole="text" style={{ color: theme.colors.textMuted, paddingVertical: 7, fontSize: 12 }}>Read-only settings. Global trading safeguards require administrator privileges.</Text> : null}
        {/* Global Safety (Kill Switch) */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>GLOBAL SAFETY</Text>
          <View style={styles.card}>
            <View style={styles.switchRow}>
              <View>
                <Text style={styles.cardTitle}>Kill Switch</Text>
                <Text style={styles.cardSubtitle}>
                  {!killKnown ? 'Status unavailable — refresh safety settings' : killSwitch.enabled ? `NEW ENTRIES HALTED: ${killSwitch.reason}` : 'Entry halt is off; other safety checks still apply'}
                </Text>
              </View>
              <Switch
                value={killSwitch.enabled}
                onValueChange={handleToggleKill}
                disabled={busy || !killKnown || !controlAllowed}
                trackColor={{ false: '#3f3f46', true: theme.colors.red }}
                thumbColor={killSwitch.enabled ? '#fff' : '#a1a1aa'}
              />
            </View>
          </View>
        </View>

        {/* Daily Loss Control */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>DAILY LOSS CONFIG</Text>
          <View style={styles.card}>
            <View style={styles.switchRow}>
              <Text style={styles.cardTitle}>Enable Circuit Breaker</Text>
              <Switch
                value={dailyLoss.enabled}
                onValueChange={(val) => setDailyLoss({ ...dailyLoss, enabled: val })}
                disabled={!controlAllowed || busy}
                trackColor={{ false: '#3f3f46', true: theme.colors.green }}
              />
            </View>

            <View style={styles.inputContainer}>
              <Text style={styles.inputLabel}>SOFT WARN LIMIT (₹)</Text>
              <TextInput
                style={styles.input}
                value={String(dailyLoss.soft_warn_inr)}
                editable={controlAllowed && !busy}
                onChangeText={(val) => setDailyLoss({ ...dailyLoss, soft_warn_inr: Number(val) || 0 })}
                keyboardType="numeric"
                placeholderTextColor={theme.colors.textMuted}
              />
            </View>

            <View style={styles.inputContainer}>
              <Text style={styles.inputLabel}>HARD HALT LIMIT (₹)</Text>
              <TextInput
                style={styles.input}
                value={String(dailyLoss.hard_halt_inr)}
                editable={controlAllowed && !busy}
                onChangeText={(val) => setDailyLoss({ ...dailyLoss, hard_halt_inr: Number(val) || 0 })}
                keyboardType="numeric"
                placeholderTextColor={theme.colors.textMuted}
              />
            </View>

            <TouchableOpacity style={styles.saveButton} disabled={busy || !lossKnown || !controlAllowed} onPress={handleSaveDailyLoss}>
              <Text style={styles.saveButtonText}>SAVE LOSS PARAMETERS</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Telegram Notifications */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>TELEGRAM NOTIFICATIONS</Text>
          <View style={styles.card}>
            <View style={styles.statusRow}>
              <Text style={styles.cardTitle}>Alert Bot Status</Text>
              <View style={styles.statusBadge}>
                <View style={[styles.dot, { backgroundColor: telegram.reachable ? theme.colors.green : theme.colors.red }]} />
                <Text style={[styles.statusText, { color: telegram.reachable ? theme.colors.green : theme.colors.red }]}>
                  {telegram.reachable ? 'CONNECTED' : 'DISCONNECTED'}
                </Text>
              </View>
            </View>

            {telegram.chat_id ? (
              <View style={styles.inputContainer}>
                <Text style={styles.inputLabel}>
                  CHAT ID{telegram.hint ? `  ·  TOKEN ···${telegram.hint}` : ''}
                </Text>
                <View style={[styles.input, { justifyContent: 'center' }]}>
                  <Text style={{ color: theme.colors.textDim, fontFamily: theme.fonts.mono, fontSize: 13 }}>
                    {telegram.chat_id}
                  </Text>
                </View>
              </View>
            ) : (
              <Text style={[styles.cardSubtitle, { marginTop: 8 }]}>
                No alert bot configured yet. Add one from the desktop app.
              </Text>
            )}

            <Text style={[styles.cardSubtitle, { marginTop: 10, lineHeight: 16 }]}>
              Alert bots are managed from the desktop app under Connect → Notifications.
            </Text>

            <View style={styles.actionsRow}>
              <TouchableOpacity style={[styles.saveButton, { flex: 1, marginRight: 8 }]} onPress={handleSaveTelegram}>
                <Text style={styles.saveButtonText}>MANAGE BOTS ↗</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.testButton, { backgroundColor: 'rgba(59, 130, 246, 0.15)', borderColor: theme.colors.blue, borderWidth: 1 }]}
                onPress={handleTestTelegram}
              >
                <Text style={[styles.saveButtonText, { color: theme.colors.blue }]}>SEND TEST ↗</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>

        {/* System Info */}
        {systemInfo && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>SYSTEM DIAGNOSTICS</Text>
            <View style={styles.card}>
              <View style={styles.diagRow}>
                <Text style={styles.diagLabel}>Version</Text>
                <Text style={styles.diagValue}>{systemInfo.version}</Text>
              </View>
              <View style={styles.diagRow}>
                <Text style={styles.diagLabel}>Environment</Text>
                <Text style={styles.diagValue}>{systemInfo.environment}</Text>
              </View>
              <View style={styles.diagRow}>
                <Text style={styles.diagLabel}>Exchange Adapter</Text>
                <Text style={styles.diagValue}>{systemInfo.exchange_adapter}</Text>
              </View>
              <View style={styles.diagRow}>
                <Text style={styles.diagLabel}>Database File</Text>
                <Text style={styles.diagValue} numberOfLines={1}>{systemInfo.db_path}</Text>
              </View>
            </View>
          </View>
        )}
        
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  center: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  scrollContainer: {
    padding: theme.spacing.md,
    paddingBottom: 40,
  },
  section: {
    marginBottom: theme.spacing.lg,
  },
  sectionTitle: {
    color: theme.colors.textMuted,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
    marginBottom: theme.spacing.sm,
  },
  modeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  modeButton: {
    flex: 1,
    height: 44,
    backgroundColor: theme.colors.bgCard,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.borderRadius.sm,
    justifyContent: 'center',
    alignItems: 'center',
    marginHorizontal: 4,
  },
  modeButtonText: {
    fontSize: 11,
    fontWeight: '800',
  },
  card: {
    backgroundColor: theme.colors.bgCard,
    borderColor: theme.colors.border,
    borderWidth: 1,
    borderRadius: theme.borderRadius.md,
    padding: theme.spacing.md,
  },
  switchRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  cardTitle: {
    color: theme.colors.textBright,
    fontSize: 13,
    fontWeight: '700',
  },
  cardSubtitle: {
    color: theme.colors.textMuted,
    fontSize: 10,
    marginTop: 2,
  },
  inputContainer: {
    marginTop: theme.spacing.md,
  },
  inputLabel: {
    color: theme.colors.textMuted,
    fontSize: 8,
    fontWeight: '800',
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  input: {
    height: 40,
    backgroundColor: theme.colors.bgHeader,
    borderColor: theme.colors.border,
    borderWidth: 1,
    borderRadius: theme.borderRadius.sm,
    paddingHorizontal: theme.spacing.sm,
    color: theme.colors.textBright,
    fontFamily: theme.fonts.mono,
    fontSize: 13,
  },
  saveButton: {
    height: 38,
    backgroundColor: theme.colors.border,
    borderRadius: theme.borderRadius.sm,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: theme.spacing.md,
  },
  saveButtonText: {
    color: theme.colors.textBright,
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  statusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
    paddingBottom: theme.spacing.sm,
    marginBottom: theme.spacing.xs,
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginRight: 6,
  },
  statusText: {
    fontSize: 10,
    fontWeight: '800',
  },
  actionsRow: {
    flexDirection: 'row',
    marginTop: theme.spacing.xs,
  },
  testButton: {
    height: 38,
    borderRadius: theme.borderRadius.sm,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: theme.spacing.md,
    paddingHorizontal: 16,
  },
  diagRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.03)',
  },
  diagLabel: {
    color: theme.colors.textMuted,
    fontSize: 11,
  },
  diagValue: {
    color: theme.colors.textDim,
    fontSize: 11,
    fontFamily: theme.fonts.mono,
  },
});

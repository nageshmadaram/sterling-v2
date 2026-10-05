import React, { useState } from 'react';
import {
  useAddTrueDataCredential,
  useDeleteTrueDataCredential,
  useRunTrueDataDiagnostics,
  useTrueDataCredentials,
  useTrueDataSettings,
  useTrueDataStatus,
  useUpdateTrueDataCredential,
  useUpdateTrueDataSettings,
} from '../../hooks/useTrueData';
import type { MarketDataSource, TrueDataCredential } from '../../types/truedata';

const S: Record<string, React.CSSProperties> = {
  card: {
    background: 'var(--k-bg)',
    border: '1px solid var(--k-border)',
    borderRadius: 9,
    padding: 18,
    marginBottom: 16,
    boxShadow: '0 1px 2px rgba(0,0,0,.025)',
  },
  title: {
    color: 'var(--k-ink-5)',
    fontSize: 10.5,
    letterSpacing: 0.75,
    marginBottom: 12,
    fontWeight: 750,
  },
  btn: {
    minHeight: 34,
    background: 'var(--k-bg)',
    color: 'var(--k-text)',
    border: '1px solid var(--k-border-strong-2)',
    padding: '0 12px',
    borderRadius: 7,
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11,
    fontWeight: 600,
  },
  btnGreen: {
    minHeight: 34,
    background: 'var(--k-brand)',
    color: 'var(--k-on-accent)',
    border: '1px solid var(--k-brand)',
    padding: '0 13px',
    borderRadius: 7,
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11,
    fontWeight: 700,
  },
  btnRed: {
    minHeight: 34,
    background: 'var(--k-bg)',
    color: 'var(--k-red-brick)',
    border: '1px solid var(--k-border-strong-2)',
    padding: '0 12px',
    borderRadius: 7,
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontSize: 11,
    fontWeight: 600,
  },
  input: {
    minHeight: 36,
    background: 'var(--k-bg)',
    color: 'var(--k-text)',
    border: '1px solid var(--k-border-strong-2)',
    borderRadius: 7,
    padding: '0 10px',
    fontFamily: 'inherit',
    fontSize: 12,
    width: '100%',
    boxSizing: 'border-box' as const,
  },
  label: {
    color: 'var(--k-ink-5)',
    fontSize: 10,
    letterSpacing: 0.7,
    marginBottom: 4,
    display: 'block',
    fontWeight: 650,
  },
  hint: { color: 'var(--k-ink-6)', fontSize: 11.5 },
  err: { color: 'var(--k-red-strong)', fontSize: 11, marginTop: 6 },
  ok: { color: 'var(--k-green)', fontSize: 11, marginTop: 6 },
};

function initials(s: string): string {
  const parts = s.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

function TrueDataCredentialCard({ cred }: { cred: TrueDataCredential }) {
  const del = useDeleteTrueDataCredential();
  const update = useUpdateTrueDataCredential();
  const runDiag = useRunTrueDataDiagnostics();

  const [expanded, setExpanded] = useState(false);
  const [edit, setEdit] = useState(false);
  const [label, setLabel] = useState(cred.label);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(cred.realtime_port || 8082);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleTest = async () => {
    setTestResult(null);
    try {
      const res = await runDiag.mutateAsync({ category_id: 'truedata_auth' });
      const authCat = res.categories?.find((c) => c.id === 'truedata_auth') || res.categories?.[0];
      if (authCat && authCat.status === 'PASS') {
        setTestResult({
          ok: true,
          message: `✓ Connection Verified (${authCat.latency_ms.toFixed(1)} ms) — WebAPI HTTP 200`,
        });
      } else {
        setTestResult({
          ok: false,
          message: `✗ ${authCat?.error_message || 'Authentication check failed'}`,
        });
      }
    } catch (err: any) {
      setTestResult({
        ok: false,
        message: `✗ ${err?.message || 'Connection failed'}`,
      });
    }
  };

  const subParts = [
    cred.username_hint ? `User: ${cred.username_hint}` : null,
    cred.is_active ? 'Active Feed' : null,
    cred.connected ? 'Connected' : 'Configured',
  ].filter(Boolean);
  const subText = subParts.join(' · ');

  return (
    <div
      style={{
        border: '1px solid var(--k-border)',
        borderRadius: 9,
        marginBottom: 16,
        overflow: 'hidden',
        background: 'var(--k-bg)',
        boxShadow: '0 1px 2px rgba(0,0,0,.025)',
      }}
    >
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '14px 16px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: '50%',
            background: 'var(--k-border-2)',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--k-ink-4)',
            fontWeight: 700,
            fontSize: 13,
          }}
        >
          {initials(cred.label)}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, color: 'var(--k-text)', fontSize: 13 }}>{cred.label}</div>
          <div style={{ color: 'var(--k-dim)', fontSize: 11, marginTop: 2 }}>{subText}</div>
        </div>
        <span
          aria-hidden
          style={{
            color: 'var(--k-faint-2)',
            fontSize: 11,
            transform: expanded ? 'rotate(180deg)' : 'none',
            transition: 'transform .15s',
          }}
        >
          ▼
        </span>
      </div>

      {expanded && (
        <div
          style={{
            borderTop: '1px solid var(--k-surface-hover-2)',
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              style={{
                ...S.btn,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                borderColor: 'var(--k-border-slate-strong)',
                background: 'var(--k-surface-sunken)',
              }}
              onClick={handleTest}
              disabled={runDiag.isPending}
            >
              {runDiag.isPending ? (
                <>
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      border: '2px solid rgba(0,0,0,0.2)',
                      borderTopColor: '#0f172a',
                      borderRadius: '50%',
                      animation: 'spin 0.6s linear infinite',
                      display: 'inline-block',
                    }}
                  />
                  Testing…
                </>
              ) : (
                '▶ Test Connection'
              )}
            </button>
            <button style={S.btn} onClick={() => setEdit((v) => !v)}>
              {edit ? 'Cancel' : 'Edit Credential'}
            </button>
            <button
              style={{ ...S.btnRed, marginLeft: 'auto' }}
              onClick={() => {
                if (window.confirm(`Remove TrueData feed "${cred.label}"?`)) del.mutate(cred.id);
              }}
            >
              Remove
            </button>
          </div>

          {testResult && (
            <div
              style={{
                fontSize: 11,
                padding: '6px 10px',
                borderRadius: 6,
                background: testResult.ok ? '#f0fdf4' : '#fef2f2',
                border: `1px solid ${testResult.ok ? '#bbf7d0' : '#fecaca'}`,
                color: testResult.ok ? '#15803d' : '#b91c1c',
                lineHeight: 1.4,
              }}
            >
              {testResult.message}
            </div>
          )}

          {edit ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <label style={S.label}>LABEL</label>
                <input
                  style={S.input}
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </div>
              <div>
                <label style={S.label}>NEW USERNAME (blank = keep current)</label>
                <input
                  style={S.input}
                  placeholder={cred.username_hint}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                />
              </div>
              <div>
                <label style={S.label}>NEW PASSWORD (blank = keep current)</label>
                <input
                  style={S.input}
                  type="password"
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label style={S.label}>REALTIME PORT</label>
                <input
                  style={S.input}
                  type="number"
                  value={port}
                  onChange={(e) => setPort(Number(e.target.value))}
                />
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button
                  style={S.btnGreen}
                  disabled={update.isPending}
                  onClick={() =>
                    update.mutate(
                      {
                        id: cred.id,
                        label: label.trim() || cred.label,
                        ...(username.trim() ? { username: username.trim() } : {}),
                        ...(password.trim() ? { password: password.trim() } : {}),
                        realtime_port: port,
                      },
                      {
                        onSuccess: () => {
                          setEdit(false);
                          setUsername('');
                          setPassword('');
                        },
                      }
                    )
                  }
                >
                  {update.isPending ? 'SAVING…' : 'SAVE CHANGES'}
                </button>
                <button
                  style={S.btn}
                  onClick={() => {
                    setEdit(false);
                    setLabel(cred.label);
                    setUsername('');
                    setPassword('');
                  }}
                >
                  CANCEL
                </button>
                <button
                  style={{ ...S.btnRed, marginLeft: 'auto' }}
                  onClick={() => {
                    if (window.confirm(`Remove TrueData feed "${cred.label}"?`)) del.mutate(cred.id);
                  }}
                >
                  DELETE
                </button>
              </div>
              {update.error && <div style={S.err}>✗ {update.error.message}</div>}
            </div>
          ) : (
            <div style={{ marginTop: 14, fontSize: 12, color: 'var(--k-ink-4)', lineHeight: 1.6 }}>
              <div><strong>Port:</strong> {cred.realtime_port || 8082}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <strong>Status:</strong>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    color: cred.connected ? 'var(--k-green-600)' : '#ea580c',
                    fontWeight: 600,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      background: cred.connected ? 'var(--k-green-600)' : '#ea580c',
                    }}
                  />
                  {cred.connected ? 'Connected & Verified' : 'Configured (Standby)'}
                </span>
              </div>
              <div><strong>Configured:</strong> {new Date(cred.created_at_ms).toLocaleDateString()}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Color tokens per source
const SOURCE_META: Record<string, { color: string; bg: string; border: string; label: string }> = {
  kite:        { color: 'var(--k-blue-kite)',   bg: 'rgba(56, 126, 209, 0.04)',  border: 'var(--k-blue-kite)',   label: 'ZERODHA KITE' },
  zerodhakite: { color: 'var(--k-blue-kite)',   bg: 'rgba(56, 126, 209, 0.04)',  border: 'var(--k-blue-kite)',   label: 'ZERODHA KITE' },
  yahoo:       { color: '#7c3aed',               bg: 'rgba(124, 58, 237, 0.04)', border: '#7c3aed',               label: 'YAHOO FINANCE' },
  truedata:    { color: 'var(--k-brand)',         bg: 'rgba(240, 100, 40, 0.04)', border: 'var(--k-brand)',        label: 'TRUEDATA' },
};

export function DataSourceSelector() {
  const { data: settings } = useTrueDataSettings();
  const update = useUpdateTrueDataSettings();
  // Normalise: backend may return legacy 'zerodhakite', treat as 'kite'
  const raw = settings?.data_source ?? 'kite';
  const currentSource: MarketDataSource = (raw === 'zerodhakite' ? 'kite' : raw) as MarketDataSource;
  const meta = SOURCE_META[currentSource] ?? SOURCE_META['kite'];

  const setSource = (source: MarketDataSource) => {
    if (source !== currentSource && !update.isPending) {
      update.mutate({ data_source: source });
    }
  };

  type OptionDef = { value: MarketDataSource; title: string; desc: string; badge?: string };
  const OPTIONS: OptionDef[] = [
    {
      value: 'kite',
      title: 'Zerodha Kite (Priority)',
      badge: 'RECOMMENDED',
      desc: 'Live broker historical data via Kite Connect. Used first for SuperTrend, Snapback, and all strategy scans. Falls back to local cache when session is inactive.',
    },
    {
      value: 'yahoo',
      title: 'Yahoo Finance',
      desc: 'Public historical OHLCV data for research and replay. Cached locally for offline use.',
    },
    {
      value: 'truedata',
      title: 'TrueData Feed',
      desc: 'Institutional tick-level data, Level 2 orderbook, and CVD/VWAP analytics. Requires separate TrueData subscription and credentials below.',
    },
  ];

  const OPTION_COLORS: Record<string, { active: string; border: string; bg: string; badge: string }> = {
    kite:     { active: 'var(--k-blue-kite)',  border: 'rgba(56,126,209,0.3)',  bg: 'rgba(56,126,209,0.04)',  badge: 'rgba(56,126,209,0.14)' },
    yahoo:    { active: '#7c3aed',              border: 'rgba(124,58,237,0.3)', bg: 'rgba(124,58,237,0.04)', badge: 'rgba(124,58,237,0.14)' },
    truedata: { active: 'var(--k-brand)',       border: 'rgba(240,100,40,0.3)', bg: 'rgba(240,100,40,0.04)', badge: 'rgba(240,100,40,0.14)' },
  };

  return (
    <div style={S.card}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={S.title}>HISTORICAL DATA SOURCE</div>
        <span
          style={{
            fontSize: 10,
            fontWeight: 750,
            letterSpacing: '0.05em',
            padding: '2px 8px',
            borderRadius: 4,
            background: `${meta.border}22`,
            color: meta.color,
            border: `1px solid ${meta.border}55`,
          }}
        >
          ACTIVE: {meta.label}
        </span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--k-ink-4)', marginBottom: 14, lineHeight: 1.5 }}>
        Choose the data source for strategy scans (SuperTrend, Snapback, Adaptive Edge). Zerodha Kite is always preferred when a valid session exists.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        {OPTIONS.map((opt) => {
          const isActive = currentSource === opt.value;
          const c = OPTION_COLORS[opt.value] ?? OPTION_COLORS['kite'];
          return (
            <div
              key={opt.value}
              id={`data-source-option-${opt.value}`}
              onClick={() => setSource(opt.value)}
              style={{
                border: `1.5px solid ${isActive ? c.active : 'var(--k-border)'}`,
                borderRadius: 8,
                padding: 14,
                background: isActive ? c.bg : 'var(--k-bg)',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
                position: 'relative',
              }}
            >
              {opt.badge && (
                <span style={{
                  position: 'absolute', top: 8, right: 10,
                  fontSize: 9, fontWeight: 750, letterSpacing: '0.06em',
                  padding: '1px 6px', borderRadius: 3,
                  background: isActive ? c.badge : 'var(--k-border-2)',
                  color: isActive ? c.active : 'var(--k-ink-5)',
                }}>
                  {opt.badge}
                </span>
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <input
                  type="radio"
                  name="market_data_source"
                  checked={isActive}
                  onChange={() => setSource(opt.value)}
                  style={{ cursor: 'pointer', accentColor: c.active }}
                />
                <span style={{ fontSize: 13, fontWeight: 700, color: isActive ? c.active : 'var(--k-ink-1)' }}>
                  {opt.title}
                </span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--k-ink-5)', lineHeight: 1.45, marginLeft: 22 }}>
                {opt.desc}
              </div>
            </div>
          );
        })}
      </div>
      {update.isPending && (
        <div style={{ fontSize: 11, color: 'var(--k-ink-6)', marginTop: 10, fontStyle: 'italic' }}>
          Updating data source preference…
        </div>
      )}
    </div>
  );
}

function AddTrueDataCredential() {
  const add = useAddTrueDataCredential();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('My TrueData Feed');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(8082);

  if (!open) {
    return (
      <button style={S.btnGreen} onClick={() => setOpen(true)}>
        + ADD TRUEDATA CREDENTIALS
      </button>
    );
  }

  return (
    <div style={S.card}>
      <div style={S.title}>NEW TRUEDATA FEED CREDENTIALS</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <label style={S.label}>FEED LABEL</label>
          <input
            style={S.input}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Primary TrueData Feed"
          />
        </div>
        <div>
          <label style={S.label}>TRUEDATA USERNAME</label>
          <input
            style={S.input}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="TrueData Username"
          />
        </div>
        <div>
          <label style={S.label}>TRUEDATA PASSWORD</label>
          <input
            style={S.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="TrueData Password"
          />
        </div>
        <div>
          <label style={S.label}>REALTIME PORT (DEFAULT: 8082)</label>
          <input
            style={S.input}
            type="number"
            value={port}
            onChange={(e) => setPort(Number(e.target.value))}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <button
            style={S.btnGreen}
            disabled={add.isPending || !username || !password}
            onClick={() =>
              add.mutate(
                {
                  label: label.trim() || 'My TrueData Feed',
                  username: username.trim(),
                  password,
                  realtime_port: port,
                },
                {
                  onSuccess: () => {
                    setOpen(false);
                    setUsername('');
                    setPassword('');
                  },
                }
              )
            }
          >
            {add.isPending ? 'CONNECTING…' : 'SAVE & CONNECT'}
          </button>
          <button style={S.btn} onClick={() => setOpen(false)}>
            CANCEL
          </button>
        </div>
        {add.error && <div style={S.err}>✗ {add.error.message}</div>}
      </div>
    </div>
  );
}

export function TrueDataCredentialsPanel() {
  const { data: creds, isLoading } = useTrueDataCredentials();
  const { data: status } = useTrueDataStatus();

  return (
    <div>
      <div style={S.card}>
        <div style={S.title}>TRUEDATA MARKET DATA STATUS</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: status?.connected ? 'var(--k-green)' : 'var(--k-ink-5)',
            }}
          />
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--k-text)' }}>
            {status?.connected
              ? `Connected (${status.username_hint || 'TrueData'})`
              : status?.message || 'Not Connected'}
          </span>
        </div>
        <div style={{ ...S.hint, marginTop: 6, lineHeight: 1.5 }}>
          TrueData provides tick and bar market data feeds for Adaptive Edge and Sterling market scanning.
          Credentials are encrypted at rest with <code>STERLING_SECRET_KEY</code> and never stored in plain text.
        </div>
      </div>

      {isLoading && <div style={S.hint}>Loading TrueData credentials…</div>}
      {creds?.map((c) => (
        <TrueDataCredentialCard key={c.id} cred={c} />
      ))}
      {creds && creds.length === 0 && (
        <div style={{ ...S.hint, marginBottom: 14 }}>
          No TrueData credentials saved yet. Add your username and password below to enable market data.
        </div>
      )}

      <AddTrueDataCredential />
    </div>
  );
}

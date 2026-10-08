import React, { useState } from 'react';
import { useRunKiteDiagnostics, useKiteDiagnosticsSummary } from '../../hooks/useKiteDiagnostics';
import { useRunTrueDataDiagnostics, useTrueDataDiagnosticsSummary } from '../../hooks/useTrueData';
import type { KiteDiagnosticCategoryResult, KiteDiagnosticSuiteResult } from '../../types/kiteDiagnostics';
import type { DiagnosticCategoryResult, DiagnosticSuiteResult } from '../../types/truedata';

export type SystemCheckItem = {
  id: string;
  source: 'kite' | 'truedata' | 'network';
  name: string;
  category: string;
  status: 'PASS' | 'FAIL' | 'WARNING' | 'IDLE' | 'RUNNING' | 'PARTIAL';
  latency_ms: number;
  symbol_tested: string;
  endpoint?: string;
  summary: string;
  source_origin: string;
  metrics: Record<string, any>;
  field_checks: Array<{ name: string; status: string; value: any; description: string }>;
  raw_sample?: Record<string, any>;
  error_message?: string | null;
  troubleshooting_tip?: string | null;
};

type CheckDefinition = Pick<SystemCheckItem, 'id' | 'source' | 'name' | 'category' | 'symbol_tested' | 'endpoint' | 'troubleshooting_tip'> & {
  field_checks: Array<Pick<SystemCheckItem['field_checks'][number], 'name' | 'description'>>;
};

function unverifiedCheck(definition: CheckDefinition): SystemCheckItem {
  return {
    ...definition,
    status: 'IDLE',
    latency_ms: 0,
    summary: 'Not yet verified — run this diagnostic.',
    source_origin: 'unverified',
    metrics: {},
    raw_sample: {},
    field_checks: definition.field_checks.map((field) => ({ ...field, status: 'IDLE', value: 'Not measured' })),
  };
}

export function SystemDiagnosticsChecklistPanel() {
  const [activeTab, setActiveTab] = useState<'ALL' | 'KITE' | 'TRUEDATA'>('ALL');
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  const { data: kiteSummary } = useKiteDiagnosticsSummary();
  const { data: tdSummary } = useTrueDataDiagnosticsSummary();

  const runKiteDiag = useRunKiteDiagnostics();
  const runTdDiag = useRunTrueDataDiagnostics();

  const [kiteResults, setKiteResults] = useState<KiteDiagnosticSuiteResult | null>(null);
  const [tdResults, setTdResults] = useState<DiagnosticSuiteResult | null>(null);

  const isRunningAll = runKiteDiag.isPending || runTdDiag.isPending;
  const isRunningKite = runKiteDiag.isPending;
  const isRunningTd = runTdDiag.isPending;

  const handleRunAll = async () => {
    try {
      const [kiteRes, tdRes] = await Promise.all([
        runKiteDiag.mutateAsync(),
        runTdDiag.mutateAsync(),
      ]);
      setKiteResults(kiteRes);
      setTdResults(tdRes);
    } catch {
      // Caught in hook
    }
  };

  const handleRunKite = async () => {
    try {
      const res = await runKiteDiag.mutateAsync();
      setKiteResults(res);
    } catch {
      // Caught in hook
    }
  };

  const handleRunTrueData = async () => {
    try {
      const res = await runTdDiag.mutateAsync();
      setTdResults(res);
    } catch {
      // Caught in hook
    }
  };

  const handleRunSingle = async (item: SystemCheckItem) => {
    setTestingId(item.id);
    try {
      if (item.source === 'kite' || item.source === 'network') {
        const res = await runKiteDiag.mutateAsync({ category_id: item.id });
        setKiteResults((prev) => {
          if (!prev) return res;
          const updated = prev.categories.map((c) =>
            c.id === item.id ? res.categories.find((nc) => nc.id === item.id) || c : c
          );
          return { ...prev, categories: updated };
        });
      } else {
        const res = await runTdDiag.mutateAsync({ category_id: item.id });
        setTdResults((prev) => {
          if (!prev) return res;
          const updated = prev.categories.map((c) =>
            c.id === item.id ? res.categories.find((nc) => nc.id === item.id) || c : c
          );
          return { ...prev, categories: updated };
        });
      }
    } finally {
      setTestingId(null);
    }
  };

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // ── Kite Baseline & Live Items ──────────────────────────────────────────────
  const defaultKiteItems: SystemCheckItem[] = ([
  {
    "id": "internet_network",
    "source": "network",
    "name": "Internet & DNS Gateway",
    "category": "Network Infrastructure",
    "symbol_tested": "8.8.8.8 / 1.1.1.1",
    "endpoint": "Socket Port 53 (TCP/UDP)",
    "troubleshooting_tip": "Ensure network interface is online and DNS servers are reachable.",
    "field_checks": [
      {
        "name": "DNS Root Handshake",
        "description": "Google primary 8.8.8.8 socket ping"
      },
      {
        "name": "Cloudflare Edge Ping",
        "description": "1.1.1.1 edge resolution"
      }
    ]
  },
  {
    "id": "kite_gateway",
    "source": "kite",
    "name": "Kite Connect HTTPS Gateway",
    "category": "Kite API Core",
    "symbol_tested": "api.kite.trade",
    "endpoint": "https://api.kite.trade",
    "troubleshooting_tip": "Check outbound HTTPS proxy or firewall rules if api.kite.trade is blocked.",
    "field_checks": [
      {
        "name": "Kite REST API Endpoint",
        "description": "Zerodha Kite Connect gateway"
      },
      {
        "name": "TLS Encryption & Cipher",
        "description": "Secure HTTPS transport layer"
      }
    ]
  },
  {
    "id": "kite_session",
    "source": "kite",
    "name": "Kite Session & User Profile",
    "category": "Kite API Core",
    "symbol_tested": "Account",
    "endpoint": "https://api.kite.trade/user/profile",
    "troubleshooting_tip": "Daily Zerodha tokens reset at 6:00 AM IST. Click \"Open Kite Login\" in Settings to generate a new session.",
    "field_checks": [
      {
        "name": "Zerodha Client ID",
        "description": "Client account identifier"
      },
      {
        "name": "Daily Access Token",
        "description": "Daily Zerodha Kite Connect access token"
      }
    ]
  },
  {
    "id": "kite_margins",
    "source": "kite",
    "name": "Kite Margins & Capital Ledger",
    "category": "Kite API Core",
    "symbol_tested": "Equity & F&O Funds",
    "endpoint": "https://api.kite.trade/user/margins",
    "troubleshooting_tip": "Login to Kite to stream real-time cash balance directly from Zerodha.",
    "field_checks": [
      {
        "name": "Intraday Trading Cash",
        "description": "Available unencumbered capital"
      },
      {
        "name": "Pledged Collateral",
        "description": "Securities collateral margin"
      }
    ]
  },
  {
    "id": "kite_instruments",
    "source": "kite",
    "name": "Master Instruments Database",
    "category": "Kite Market Data",
    "symbol_tested": "NSE / NFO Catalog",
    "endpoint": "Local Memory / Cache",
    "troubleshooting_tip": "Daily instrument tokens automatically synchronize at 08:30 AM IST before market open.",
    "field_checks": [
      {
        "name": "Tradable Instruments",
        "description": "Master instrument symbol mapping"
      },
      {
        "name": "Segment Resolvers",
        "description": "Active trading segments"
      }
    ]
  },
  {
    "id": "kite_historical",
    "source": "kite",
    "name": "Kite Historical 5m Candle Feed",
    "category": "Kite Market Data",
    "symbol_tested": "NIFTY 50 (Token 256265)",
    "endpoint": "https://api.kite.trade/instruments/historical/256265/5minute",
    "troubleshooting_tip": "Historical data needs the Kite Connect Historical API add-on. Without it Sterling falls back to the offline lake.",
    "field_checks": [
      {
        "name": "Historical Candle Stream",
        "description": "5-minute historical OHLCV candles"
      },
      {
        "name": "Last Candle Close",
        "description": "Latest verified candle close"
      }
    ]
  },
  {
    "id": "kite_quotes",
    "source": "kite",
    "name": "Kite Live Quotes & L1 Depth",
    "category": "Kite Market Data",
    "symbol_tested": "NSE:NIFTY 50",
    "endpoint": "https://api.kite.trade/quote?i=NSE:NIFTY+50",
    "troubleshooting_tip": "Quotes API supplies sub-second price triggers for autonomous order entry.",
    "field_checks": [
      {
        "name": "Spot Quote LTP",
        "description": "Top-of-book real-time price"
      },
      {
        "name": "Spread Bounds",
        "description": "Level 1 order book depth"
      }
    ]
  },
  {
    "id": "kite_orders_gtt",
    "source": "kite",
    "name": "Kite Orders & GTT Safety Subsystem",
    "category": "Execution & Safety",
    "symbol_tested": "Order Router",
    "endpoint": "https://api.kite.trade/gtt/triggers",
    "troubleshooting_tip": "GTT autonomous triggers protect orders on exchange servers regardless of WebSocket connectivity.",
    "field_checks": [
      {
        "name": "Execution Safety Layer",
        "description": "Trade execution safety layer"
      },
      {
        "name": "GTT Rules Engine",
        "description": "Autonomous trigger safety"
      }
    ]
  }
] satisfies CheckDefinition[]).map(unverifiedCheck);

  // ── TrueData Baseline & Live Items ──────────────────────────────────────────
  const defaultTdItems: SystemCheckItem[] = ([
  {
    "id": "truedata_auth",
    "source": "truedata",
    "name": "TrueData REST WebAPI & Auth Handshake",
    "category": "TrueData Feed Gateway",
    "symbol_tested": "Account",
    "endpoint": "https://history.truedata.in/getlastbar",
    "troubleshooting_tip": "Go to Settings > TrueData Feed to configure or test your username and password.",
    "field_checks": [
      {
        "name": "REST WebAPI Token",
        "description": "TrueData REST HTTP token validation"
      },
      {
        "name": "Socket Port Allocation",
        "description": "Real-time feed streaming port"
      }
    ]
  },
  {
    "id": "indices",
    "source": "truedata",
    "name": "Indices Feed (Spot OHLC)",
    "category": "Market Feeds",
    "symbol_tested": "NIFTY 50",
    "endpoint": "https://history.truedata.in/getlastnbars",
    "troubleshooting_tip": "Indices carry 0 volume by design on NSE; ATR volatility expansion protects triggers.",
    "field_checks": [
      {
        "name": "Spot Price LTP",
        "description": "Positive index quote"
      },
      {
        "name": "Zero-Volume Handler",
        "description": "Index volatility filter"
      }
    ]
  },
  {
    "id": "equity_spot",
    "source": "truedata",
    "name": "Cash Equities Feed",
    "category": "Market Feeds",
    "symbol_tested": "RELIANCE",
    "endpoint": "https://history.truedata.in/getlastnbars",
    "troubleshooting_tip": "Requires active TrueData NSE Cash segment entitlement.",
    "field_checks": [
      {
        "name": "Equity Spot LTP",
        "description": "Traded price"
      },
      {
        "name": "Traded Volume",
        "description": "Non-zero volume stream"
      }
    ]
  },
  {
    "id": "futures",
    "source": "truedata",
    "name": "Derivatives & Near-Month Futures",
    "category": "Market Feeds",
    "symbol_tested": "NIFTY-I Futures",
    "endpoint": "https://history.truedata.in/getlastnbars",
    "troubleshooting_tip": "Futures feed requires TrueData NFO Futures subscription.",
    "field_checks": [
      {
        "name": "Futures LTP",
        "description": "Continuous near contract"
      },
      {
        "name": "Open Interest (OI)",
        "description": "Active OI tracking"
      }
    ]
  },
  {
    "id": "options_chain",
    "source": "truedata",
    "name": "Options Chain & Strike Ladder",
    "category": "Market Feeds",
    "symbol_tested": "NIFTY Strike Ladder",
    "endpoint": "https://marketdata.truedata.in/getOptionChain",
    "troubleshooting_tip": "Options chain requires TrueData NFO Options segment entitlement.",
    "field_checks": [
      {
        "name": "ATM Strike Resolution",
        "description": "Closest At-The-Money strike"
      },
      {
        "name": "Put-Call Ratio (PCR)",
        "description": "Total Put OI / Call OI"
      }
    ]
  },
  {
    "id": "volume_tape",
    "source": "truedata",
    "name": "Volume & RVOL Tape Analyzer",
    "category": "Quantitative Engines",
    "symbol_tested": "Volume Tape",
    "endpoint": "Analytical Solver",
    "troubleshooting_tip": "RVOL gates Adaptive Edge breakout entries to prevent low-liquidity whipsaws.",
    "field_checks": [
      {
        "name": "Current Bar Volume",
        "description": "Latest 5m volume"
      },
      {
        "name": "RVOL Factor",
        "description": "Volume expansion vs 20-period baseline"
      }
    ]
  },
  {
    "id": "options_greeks",
    "source": "truedata",
    "name": "Options Greeks Solver",
    "category": "Quantitative Engines",
    "symbol_tested": "BSM Analytical Engine",
    "endpoint": "Black-Scholes 76 Solver",
    "troubleshooting_tip": "Greeks engine computes continuous delta decay for automated position rebalancing.",
    "field_checks": [
      {
        "name": "Call Delta (Δ)",
        "description": "Sensitivity to underlying price"
      },
      {
        "name": "Theta Decay (Θ)",
        "description": "Daily time value decay"
      }
    ]
  },
  {
    "id": "market_profile",
    "source": "truedata",
    "name": "Market Profile Engine",
    "category": "Quantitative Engines",
    "symbol_tested": "TPO Structure",
    "endpoint": "TPO Profile Analyzer",
    "troubleshooting_tip": "Market Profile establishes auction equilibrium zones for intraday mean-reversion filters.",
    "field_checks": [
      {
        "name": "Point of Control (POC)",
        "description": "Highest TPO price acceptance"
      },
      {
        "name": "Value Area High (VAH)",
        "description": "70% value area upper bound"
      }
    ]
  },
  {
    "id": "volume_profile",
    "source": "truedata",
    "name": "Volume Profile & Order Imbalance",
    "category": "Quantitative Engines",
    "symbol_tested": "Volume Profile",
    "endpoint": "VPOC & Imbalance Analyzer",
    "troubleshooting_tip": "Volume profile anchors trailing profit targets at high-volume nodes.",
    "field_checks": [
      {
        "name": "Volume POC (VPOC)",
        "description": "High-volume node anchor"
      },
      {
        "name": "Buyer / Seller Imbalance",
        "description": "Aggressor flow ratio"
      }
    ]
  },
  {
    "id": "delta_orderflow",
    "source": "truedata",
    "name": "Delta & Microstructure Aggression",
    "category": "Quantitative Engines",
    "symbol_tested": "TBT Order Flow",
    "endpoint": "CVD Microstructure Engine",
    "troubleshooting_tip": "TBT delta aggression gates high-conviction momentum breakout execution.",
    "field_checks": [
      {
        "name": "Cumulative Volume Delta",
        "description": "Tick-by-tick buyer aggressor delta"
      },
      {
        "name": "Order Flow Conviction",
        "description": "Market buy/sell aggression direction"
      }
    ]
  }
] satisfies CheckDefinition[]).map(unverifiedCheck);

  // Static rows define labels/help only. Until a diagnostic actually runs,
  // no sample number or placeholder may count as evidence.
  const asUnverified = (item: SystemCheckItem): SystemCheckItem => ({
    ...item,
    status: 'IDLE',
    latency_ms: 0,
    summary: 'Not yet verified — run this diagnostic to collect current evidence.',
    metrics: {},
    field_checks: item.field_checks.map((field) => ({
      ...field,
      status: 'IDLE',
      value: 'Not measured',
    })),
    raw_sample: {},
    error_message: null,
  });

  // Merge live test results into items
  const mergeKiteItems = (): SystemCheckItem[] => {
    if (!kiteResults) return defaultKiteItems.map(asUnverified);
    return defaultKiteItems.map((item) => {
      const live = kiteResults.categories.find((c) => c.id === item.id);
      if (!live) return asUnverified(item);
      return {
        ...item,
        status: live.status as any,
        latency_ms: live.latency_ms,
        source_origin: live.source_origin,
        symbol_tested: live.symbol_tested,
        summary: live.summary,
        metrics: live.metrics ?? {},
        field_checks: live.field_checks ?? [],
        raw_sample: (live as any).raw_sample ?? {},
        error_message: live.error_message,
        troubleshooting_tip: live.troubleshooting_tip || item.troubleshooting_tip,
      };
    });
  };

  const mergeTdItems = (): SystemCheckItem[] => {
    if (!tdResults) return defaultTdItems.map(asUnverified);
    return defaultTdItems.map((item) => {
      const live = tdResults.categories.find((c) => c.id === item.id);
      if (!live) return asUnverified(item);
      return {
        ...item,
        status: live.status as any,
        latency_ms: live.latency_ms,
        source_origin: live.source_origin,
        symbol_tested: live.symbol_tested,
        summary: live.summary,
        metrics: live.metrics ?? {},
        field_checks: live.field_checks ?? [],
        raw_sample: live.raw_sample ?? {},
        error_message: live.error_message,
        troubleshooting_tip: live.troubleshooting_tip || item.troubleshooting_tip,
      };
    });
  };

  const kiteItems = mergeKiteItems();
  const tdItems = mergeTdItems();
  const allItems = [...kiteItems, ...tdItems];

  // Stats calculation
  const totalCheckpoints = allItems.length;
  const verifiedCount = allItems.filter((i) => i.status === 'PASS').length;
  const warningCount = allItems.filter((i) => i.status === 'WARNING' || i.status === 'PARTIAL').length;
  const failedCount = allItems.filter((i) => i.status === 'FAIL').length;

  const renderStatusBadge = (status: string, itemId: string, isTesting: boolean) => {
    if (isTesting) {
      return (
        <span className="diag-badge diag-badge-testing">
          <span className="diag-spinner-tiny" />
          TESTING...
        </span>
      );
    }
    if (status === 'PASS') {
      return (
        <span className="diag-badge-tick" title="Verified" aria-label="Verified">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ display: 'block' }}>
            <circle cx="8" cy="8" r="7" fill="var(--k-green-600)" />
            <path d="M4.8 8.2l2.2 2.2 4.4-4.8" stroke="var(--k-bg)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      );
    }
    if (status === 'FAIL') {
      return <span className="diag-badge diag-badge-fail">FAILED</span>;
    }
    if (status === 'IDLE') {
      return <span className="diag-badge diag-badge-warn">NOT RUN</span>;
    }
    if (itemId === 'kite_session' || itemId === 'truedata_auth') {
      return <span className="diag-badge diag-badge-auth">LOGIN REQUIRED</span>;
    }
    if (itemId === 'kite_historical' || itemId === 'kite_quotes' || itemId === 'indices' || itemId === 'equity_spot' || itemId === 'futures' || itemId === 'options_chain') {
      return <span className="diag-badge diag-badge-warn">FALLBACK (LAKE)</span>;
    }
    return <span className="diag-badge diag-badge-warn">WARNING</span>;
  };

  const renderSectionTable = (title: string, subtitle: string, items: SystemCheckItem[], onRunSection: () => void, isSectionRunning: boolean, providerType: 'KITE' | 'TRUEDATA') => {
    return (
      <div className="diag-section-block">
        <div className="diag-section-header">
          <div>
            <div className="diag-section-title">{title}</div>
            <div className="diag-section-sub">{subtitle}</div>
          </div>
          <button
            className="diag-btn diag-btn-secondary"
            onClick={onRunSection}
            disabled={isSectionRunning || isRunningAll}
          >
            {isSectionRunning ? (
              <>
                <span className="diag-spinner-tiny" />
                Running {providerType === 'KITE' ? 'Kite' : 'TrueData'} Suite...
              </>
            ) : (
              `▶ Run ${providerType === 'KITE' ? 'Kite' : 'TrueData'} Suite`
            )}
          </button>
        </div>

        <div className="diag-table-header">
          <div className="diag-th diag-th-dot" />
          <div className="diag-th diag-th-name">Checkpoint & Scope</div>
          <div className="diag-th diag-th-target">Target / Endpoint</div>
          <div className="diag-th diag-th-latency">Latency</div>
          <div className="diag-th diag-th-status">Health Status</div>
          <div className="diag-th diag-th-actions">Actions</div>
        </div>

        <div className="diag-items-list">
          {items.map((item) => {
            const isExpanded = !!expandedIds[item.id];
            const isTesting = testingId === item.id;

            return (
              <div key={item.id} className={`diag-item-card ${isExpanded ? 'is-expanded' : ''}`}>
                <div className="diag-item-row" onClick={() => toggleExpand(item.id)}>
                  <div className="diag-status-indicator">
                    <span className={`diag-status-dot dot-${isTesting ? 'running' : item.status.toLowerCase()}`} />
                  </div>

                  <div className="diag-item-main">
                    <div className="diag-item-name">{item.name}</div>
                    <div className="diag-item-summary">{item.summary}</div>
                  </div>

                  <div className="diag-item-target-col">
                    <span className="diag-target-code" title={item.symbol_tested}>{item.symbol_tested}</span>
                  </div>

                  <div className="diag-item-latency-col">
                    <span className="diag-latency-tag">{item.latency_ms.toFixed(1)} ms</span>
                  </div>

                  <div className="diag-item-status-col">
                    <div className="diag-badge-slot">
                      {renderStatusBadge(item.status, item.id, isTesting)}
                    </div>
                  </div>

                  <div className="diag-item-actions-col" onClick={(e) => e.stopPropagation()}>
                    <button
                      className="diag-btn-micro"
                      onClick={() => handleRunSingle(item)}
                      disabled={isTesting || isRunningAll}
                      title="Run single verification check"
                    >
                      {isTesting ? <span className="diag-spinner-tiny" /> : 'Test'}
                    </button>
                  </div>
                </div>

                {/* ── Expanded Verifiable Proof Drawer ── */}
                {isExpanded && (
                  <div className="diag-proof-drawer">
                    <div className="diag-drawer-meta-bar">
                      <div className="diag-meta-cell">
                        <span className="diag-meta-label">Target Endpoint</span>
                        <span className="diag-meta-val">{item.endpoint || item.symbol_tested}</span>
                      </div>
                      <div className="diag-meta-cell">
                        <span className="diag-meta-label">Data Origin</span>
                        <span className="diag-meta-val">{item.source_origin}</span>
                      </div>
                      <div className="diag-meta-cell">
                        <span className="diag-meta-label">Round-Trip Latency</span>
                        <span className="diag-meta-val">{item.latency_ms.toFixed(2)} ms</span>
                      </div>
                    </div>

                    {/* Verified Health Checks Grid */}
                    {item.field_checks.length > 0 && (
                      <div className="diag-drawer-subblock">
                        <div className="diag-subblock-title">Verified Field Health Checks</div>
                        <div className="diag-fields-grid">
                          {item.field_checks.map((fc, idx) => (
                            <div key={idx} className="diag-field-box">
                              <div>
                                <div className="diag-field-name">{fc.name}</div>
                                <div className="diag-field-desc">{fc.description}</div>
                              </div>
                              <span className={`diag-field-val val-${fc.status.toLowerCase()}`}>
                                {String(fc.value)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Verifiable Server Proof Payload */}
                    {item.raw_sample && Object.keys(item.raw_sample).length > 0 && (
                      <div className="diag-drawer-subblock">
                        <div className="diag-subblock-title">Verifiable Server Telemetry & Proof Payload</div>
                        <pre className="diag-payload-code">
                          {JSON.stringify(item.raw_sample, null, 2)}
                        </pre>
                      </div>
                    )}

                    {/* Error & Troubleshooting Notice */}
                    {item.error_message && (
                      <div className="diag-error-box">
                        <div className="diag-error-title">
                          {item.source === 'truedata' ? 'TrueData Gateway Error' : (item.source === 'kite' ? 'Zerodha Kite Error' : 'Network Error')}
                        </div>
                        <div className="diag-error-text">{item.error_message}</div>
                      </div>
                    )}

                    {item.troubleshooting_tip && (
                      <div className="diag-tip-box">
                        <strong>Remediation Tip:</strong> {item.troubleshooting_tip}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="diag-cockpit-container">
      {/* ── Top Header & Global Actions ── */}
      <div className="diag-cockpit-header">
        <div>
          <h2 className="diag-main-title">Feed & API Health & Verification Checklist</h2>
          <p className="diag-main-sub">
            Broker session, market-data feeds and analytics engines.
          </p>
        </div>

        <div className="diag-top-actions">
          <button
            className="diag-btn diag-btn-primary"
            onClick={handleRunAll}
            disabled={isRunningAll}
          >
            {isRunningAll ? (
              <>
                <span className="diag-spinner-tiny" />
                Verifying All Checkpoints...
              </>
            ) : (
              'Run All Verification Checks'
            )}
          </button>
        </div>
      </div>

      {/* ── Status Overview Bar ── */}
      <div className="diag-overview-strip">
        <div className="diag-stat-card">
          <span className="diag-stat-label">System Health Status</span>
          <div className="diag-stat-main">
            <span className="diag-stat-number">{verifiedCount}</span>
            <span className="diag-stat-total">/ {totalCheckpoints} Verified</span>
          </div>
          <span className="diag-stat-sub">
            {warningCount > 0 ? `${warningCount} Fallback / Warning` : 'All Systems Verified'}
            {failedCount > 0 ? ` • ${failedCount} Failed` : ''}
          </span>
        </div>

        <div className="diag-stat-card">
          <span className="diag-stat-label">Zerodha Kite API</span>
          <div className="diag-stat-main">
            <span className={`diag-dot-inline ${kiteSummary?.authenticated ? 'dot-active' : 'dot-warn'}`} />
            <span className="diag-stat-text">
              {kiteSummary?.authenticated ? 'Connected & Active' : (kiteSummary?.has_credentials ? 'Session Idle' : 'Not Configured')}
            </span>
          </div>
          <span className="diag-stat-sub">
            Execution: {kiteSummary?.authenticated ? 'Live Broker Direct' : 'Unknown'}
          </span>
        </div>

        <div className="diag-stat-card">
          <span className="diag-stat-label">TrueData Market Pipeline</span>
          <div className="diag-stat-main">
            <span className={`diag-dot-inline ${tdSummary?.authenticated ? 'dot-active' : 'dot-warn'}`} />
            <span className="diag-stat-text">
              {tdSummary?.authenticated
                ? 'Live Stream Active'
                : tdSummary?.has_credentials ? 'Live Feed Not Verified' : 'Not Configured'}
            </span>
          </div>
          <span className="diag-stat-sub">
            {tdSummary?.authenticated
              ? `Port ${tdSummary.realtime_port || 8084} Connected`
              : 'Historical/self-test paths do not certify live market data'}
          </span>
        </div>
      </div>

      {/* ── Navigation Tabs ── */}
      <div className="diag-tabs-nav">
        <button
          className={`diag-tab-btn ${activeTab === 'ALL' ? 'active' : ''}`}
          onClick={() => setActiveTab('ALL')}
        >
          All Checkpoints ({allItems.length})
        </button>
        <button
          className={`diag-tab-btn ${activeTab === 'KITE' ? 'active' : ''}`}
          onClick={() => setActiveTab('KITE')}
        >
          Zerodha Kite & Network ({kiteItems.length})
        </button>
        <button
          className={`diag-tab-btn ${activeTab === 'TRUEDATA' ? 'active' : ''}`}
          onClick={() => setActiveTab('TRUEDATA')}
        >
          TrueData Market Feeds ({tdItems.length})
        </button>
      </div>

      {/* ── Content Views ── */}
      <div className="diag-content-area">
        {(activeTab === 'ALL' || activeTab === 'KITE') && (
          renderSectionTable(
            'Zerodha Kite & Network Execution Stack',
            'Network latency, Kite REST gateway, session, margins and order safety.',
            kiteItems,
            handleRunKite,
            isRunningKite,
            'KITE'
          )
        )}

        {(activeTab === 'ALL' || activeTab === 'TRUEDATA') && (
          renderSectionTable(
            'TrueData Market Data & Analytical Engines',
            'Live ticks, option chain, Greeks, volume profile and order flow.',
            tdItems,
            handleRunTrueData,
            isRunningTd,
            'TRUEDATA'
          )
        )}
      </div>

      {/* ── Component Styles ── */}
      <style>{`
        .diag-cockpit-container {
          display: flex;
          flex-direction: column;
          gap: 16px;
          max-width: 1000px;
          margin: 0 auto;
          color: var(--k-ink-slate-1);
          font-family: inherit;
        }

        .diag-cockpit-header {
          background: var(--k-bg);
          border: 1px solid var(--k-border-slate);
          border-radius: 8px;
          padding: 16px 20px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 12px;
        }

        .diag-main-title {
          font-size: 15px;
          font-weight: 600;
          color: var(--k-ink-slate-1);
          margin: 0;
          letter-spacing: -0.01em;
        }

        .diag-main-sub {
          font-size: 12px;
          color: var(--k-ink-slate-3);
          margin: 3px 0 0 0;
          line-height: 1.4;
        }

        .diag-top-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .diag-btn {
          font-size: 12px;
          font-weight: 500;
          border-radius: 6px;
          padding: 7px 14px;
          cursor: pointer;
          transition: all 0.15s ease;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          line-height: 1;
        }

        .diag-btn-primary {
          background: var(--k-brand);
          color: var(--k-bg);
          border: 1px solid #e05320;
          box-shadow: 0 1px 2px rgba(240, 100, 40, 0.2);
        }

        .diag-btn-primary:hover:not(:disabled) {
          background: #e05320;
          border-color: #d64a1d;
        }

        .diag-btn-secondary {
          background: var(--k-surface-warm);
          color: var(--k-brand);
          border: 1px solid var(--k-tint-warm);
        }

        .diag-btn-secondary:hover:not(:disabled) {
          background: var(--k-tint-warm-2);
          border-color: #fca889;
        }

        .diag-btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .diag-overview-strip {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 12px;
        }

        .diag-stat-card {
          background: var(--k-bg);
          border: 1px solid var(--k-border-slate);
          border-radius: 8px;
          padding: 12px 16px;
          display: flex;
          flex-direction: column;
          gap: 3px;
          transition: border-color 0.15s ease;
        }

        .diag-stat-card:hover {
          border-color: var(--k-tint-warm);
        }

        .diag-stat-label {
          font-size: 11px;
          font-weight: 500;
          color: var(--k-ink-slate-3);
          text-transform: uppercase;
          letter-spacing: 0.03em;
        }

        .diag-stat-main {
          display: flex;
          align-items: baseline;
          gap: 6px;
        }

        .diag-stat-number {
          font-size: 16px;
          font-weight: 600;
          color: var(--k-brand);
        }

        .diag-stat-total {
          font-size: 12px;
          color: var(--k-ink-slate-3);
        }

        .diag-stat-text {
          font-size: 13px;
          font-weight: 500;
          color: var(--k-ink-slate-1);
        }

        .diag-stat-sub {
          font-size: 11.5px;
          color: var(--k-ink-slate-3);
        }

        .diag-dot-inline {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          display: inline-block;
          margin-right: 4px;
        }

        .dot-active { background: var(--k-green-600); }
        .dot-warn { background: var(--k-warn); }

        .diag-tabs-nav {
          display: flex;
          gap: 4px;
          border-bottom: 1px solid var(--k-border-slate);
          padding-bottom: 0;
        }

        .diag-tab-btn {
          background: none;
          border: none;
          border-bottom: 2px solid transparent;
          font-size: 12px;
          font-weight: 500;
          color: var(--k-ink-slate-3);
          padding: 8px 14px;
          cursor: pointer;
          transition: all 0.15s ease;
          border-radius: 4px 4px 0 0;
        }

        .diag-tab-btn:hover {
          color: var(--k-brand);
          background: var(--k-surface-warm);
        }

        .diag-tab-btn.active {
          color: var(--k-brand);
          border-bottom-color: var(--k-brand);
          background: var(--k-surface-warm);
          font-weight: 600;
        }

        .diag-content-area {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }

        .diag-section-block {
          background: var(--k-bg);
          border: 1px solid var(--k-border-slate);
          border-radius: 8px;
          overflow: hidden;
        }

        .diag-section-header {
          padding: 12px 16px;
          background: var(--k-surface-sunken);
          border-bottom: 1px solid var(--k-border-slate);
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        }

        .diag-section-title {
          font-size: 13px;
          font-weight: 600;
          color: var(--k-ink-slate-1);
          letter-spacing: -0.01em;
        }

        .diag-section-sub {
          font-size: 11.5px;
          color: var(--k-ink-slate-3);
          margin-top: 2px;
        }

        .diag-table-header {
          display: grid;
          grid-template-columns: 20px minmax(220px, 1.6fr) 150px 80px 110px 70px;
          align-items: center;
          padding: 8px 16px;
          gap: 12px;
          background: var(--k-surface-sunken);
          border-bottom: 1px solid var(--k-border-slate);
          user-select: none;
        }

        .diag-th {
          font-size: 10px;
          font-weight: 700;
          color: var(--k-ink-slate-3);
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .diag-th-latency {
          text-align: right;
        }

        .diag-th-status {
          text-align: center;
        }

        .diag-th-actions {
          text-align: center;
        }

        .diag-items-list {
          display: flex;
          flex-direction: column;
        }

        .diag-item-card {
          border-bottom: 1px solid var(--k-surface-slate);
          transition: background 0.15s ease;
        }

        .diag-item-card:last-child {
          border-bottom: none;
        }

        .diag-item-card.is-expanded {
          background: #fafbfc;
        }

        .diag-item-row {
          display: grid;
          grid-template-columns: 20px minmax(220px, 1.6fr) 150px 80px 110px 70px;
          align-items: center;
          padding: 10px 16px;
          gap: 12px;
          cursor: pointer;
        }

        .diag-item-row:hover {
          background: var(--k-surface-sunken);
        }

        .diag-status-indicator {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 20px;
        }

        .diag-status-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          display: inline-block;
        }

        .dot-pass { background: var(--k-green-600); }
        .dot-warning, .dot-partial { background: var(--k-warn); }
        .dot-fail { background: var(--k-red-deep); }
        .dot-idle { background: var(--k-ink-slate-4); }
        .dot-running {
          background: transparent;
          border: 2px solid var(--k-brand);
          border-top-color: transparent;
          animation: spin 0.6s linear infinite;
        }

        .diag-item-main {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .diag-item-name {
          font-size: 12.5px;
          font-weight: 500;
          color: #0f172a;
          line-height: 1.25;
        }

        .diag-item-summary {
          font-size: 11.5px;
          color: var(--k-ink-slate-3);
          line-height: 1.35;
        }

        .diag-item-target-col {
          display: flex;
          align-items: center;
          justify-content: flex-start;
          min-width: 0;
        }

        .diag-target-code {
          font-size: 10.5px;
          font-family: monospace;
          color: var(--k-brand);
          background: var(--k-surface-warm);
          padding: 2px 7px;
          border-radius: 4px;
          border: 1px solid var(--k-tint-warm);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 150px;
          display: inline-block;
        }

        .diag-item-latency-col {
          display: flex;
          align-items: center;
          justify-content: flex-end;
        }

        .diag-latency-tag {
          font-size: 11px;
          color: var(--k-ink-slate-4);
          font-family: monospace;
          white-space: nowrap;
          text-align: right;
        }

        .diag-item-status-col {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;
        }

        .diag-item-actions-col {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;
        }

        .diag-badge-slot {
          display: flex;
          align-items: center;
          justify-content: center;
          width: 100%;
        }

        .diag-badge-tick {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 18px;
          height: 18px;
        }

        .diag-badge {
          font-size: 10px;
          font-weight: 500;
          padding: 2px 7px;
          border-radius: 4px;
          letter-spacing: 0.02em;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          white-space: nowrap;
        }

        .diag-badge-fail { background: #fef2f2; color: var(--k-red-deep); border: 1px solid #fecaca; }
        .diag-badge-warn { background: #fffbeb; color: var(--k-warn); border: 1px solid #fde68a; }
        .diag-badge-auth { background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa; }
        .diag-badge-testing { background: var(--k-surface-warm); color: var(--k-brand); border: 1px solid var(--k-tint-warm); }

        .diag-btn-micro {
          font-size: 11px;
          font-weight: 500;
          background: var(--k-surface-sunken);
          border: 1px solid var(--k-border-slate-strong);
          color: var(--k-brand);
          padding: 3px 10px;
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.15s ease;
        }

        .diag-btn-micro:hover:not(:disabled) {
          background: var(--k-surface-warm);
          border-color: var(--k-tint-warm);
        }

        .diag-proof-drawer {
          padding: 14px 16px 14px 42px;
          border-top: 1px solid var(--k-surface-slate);
          background: #fafbfc;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .diag-drawer-meta-bar {
          display: flex;
          gap: 20px;
          flex-wrap: wrap;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--k-border-slate);
        }

        .diag-meta-cell {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .diag-meta-label {
          font-size: 10px;
          font-weight: 500;
          color: var(--k-ink-slate-3);
          text-transform: uppercase;
        }

        .diag-meta-val {
          font-size: 11.5px;
          font-family: monospace;
          color: var(--k-ink-slate-1);
        }

        .diag-drawer-subblock {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .diag-subblock-title {
          font-size: 11px;
          font-weight: 500;
          color: #475569;
          text-transform: uppercase;
          letter-spacing: 0.02em;
        }

        .diag-fields-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 8px;
        }

        .diag-field-box {
          background: var(--k-bg);
          border: 1px solid var(--k-border-slate);
          border-radius: 6px;
          padding: 8px 12px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
        }

        .diag-field-name {
          font-size: 11.5px;
          font-weight: 500;
          color: var(--k-ink-slate-1);
        }

        .diag-field-desc {
          font-size: 10.5px;
          color: var(--k-ink-slate-3);
        }

        .diag-field-val {
          font-size: 11.5px;
          font-weight: 500;
          font-family: monospace;
        }

        .val-pass { color: var(--k-green-600); }
        .val-warning, .val-partial { color: var(--k-warn); }
        .val-fail { color: var(--k-red-deep); }
        .val-idle { color: var(--k-ink-slate-3); }

        .diag-payload-code {
          background: #0f172a;
          color: var(--k-border-slate);
          padding: 10px 14px;
          border-radius: 6px;
          font-size: 11px;
          font-family: monospace;
          margin: 0;
          max-height: 180px;
          overflow-y: auto;
          line-height: 1.4;
        }

        .diag-error-box {
          background: #fef2f2;
          border: 1px solid #fecaca;
          border-radius: 6px;
          padding: 8px 12px;
          font-size: 11.5px;
          color: #991b1b;
        }

        .diag-error-title {
          font-weight: 600;
          margin-bottom: 2px;
        }

        .diag-error-text {
          font-family: monospace;
          font-size: 11px;
        }

        .diag-tip-box {
          background: #fffbeb;
          border: 1px solid #fde68a;
          border-radius: 6px;
          padding: 8px 12px;
          font-size: 11.5px;
          color: #92400e;
          line-height: 1.4;
        }

        .diag-spinner-tiny {
          width: 10px;
          height: 10px;
          border: 1.5px solid currentColor;
          border-top-color: transparent;
          border-radius: 50%;
          display: inline-block;
          animation: spin 0.6s linear infinite;
        }

        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

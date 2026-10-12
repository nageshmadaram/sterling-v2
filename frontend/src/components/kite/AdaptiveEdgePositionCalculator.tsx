import React, { useMemo, useState } from 'react';
import { k } from '../../styles/kiteUI';
import { roundToTick, fmtTick, fmtINR } from '../../utils/fmt';
import { formatInstrument } from '../../lib/instrument';
import { useOrderWindowStore } from '../../store/useOrderWindowStore';

/** Premium to the 0.05 tick, up to two decimals, Indian grouping; "—" when unknown. */
function fmt(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value) || !Number.isFinite(value)) return '—';
  const tickRounded = digits === 2 ? Math.round(Number(value) * 20) / 20 : Number(value);
  return tickRounded.toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/** Lot sizes change by contract and expiry; callers must supply the instrument master value. */
export function getInstrumentLotSize(_symbol: string): number { return 0; }

interface Props {
  symbol: string;
  tradingsymbol?: string;
  exchange?: string;
  expiry?: string | null;
  lotSize?: number | null;
  defaultLots?: number | null;
  defaultEntryPrice?: number | null;
  defaultSl?: number | null;
  defaultTsl?: number | null;
  defaultExit?: number | null;
  currentLtp?: number | null;
  optionType?: 'CE' | 'PE';
  exitState?: string | null;
  tag?: string;
  hideTsl?: boolean;
  /** False makes the calculator a read-only plan viewer: no generic order ticket. */
  allowOrder?: boolean;
  /** Strategy-owned action replaces the generic ticket without bypassing admission. */
  executionAction?: React.ReactNode;
  unavailableReason?: string;
}

export function AdaptiveEdgePositionCalculator({
  symbol,
  tradingsymbol,
  exchange = 'NFO',
  expiry,
  lotSize: propLotSize,
  defaultLots,
  defaultEntryPrice,
  defaultSl,
  defaultTsl,
  defaultExit,
  currentLtp,
  optionType = 'CE',
  exitState,
  tag = 'ADAPTIVE_EDGE',
  hideTsl = false,
  allowOrder = true,
  executionAction,
  unavailableReason,
}: Props) {
  const openOrderWindow = useOrderWindowStore((s) => s.openOrderWindow);
  const [copied, setCopied] = useState(false);
  const lotSize = useMemo(
    () => (propLotSize != null && propLotSize > 0 ? propLotSize : getInstrumentLotSize(symbol)),
    [propLotSize, symbol],
  );
  const baseEntry = roundToTick(defaultEntryPrice ?? currentLtp ?? 100) ?? 100;
  const baseSl = roundToTick(defaultSl ?? (baseEntry * 0.8)) ?? Number((baseEntry * 0.8).toFixed(2));
  const baseTsl = roundToTick(defaultTsl ?? baseEntry) ?? baseEntry;
  const baseTarget = roundToTick(defaultExit ?? 0) ?? baseEntry;

  // Editable State
  const plannedLots = defaultLots != null && defaultLots > 0 ? Math.round(defaultLots) : 1;
  const [numLots, setNumLots] = useState<number>(plannedLots);
  const [entryPrice, setEntryPrice] = useState<number>(baseEntry);
  const [slPrice, setSlPrice] = useState<number>(baseSl);
  const [tslPrice, setTslPrice] = useState<number>(baseTsl);
  const [targetPrice, setTargetPrice] = useState<number>(baseTarget);

  const isCustomized =
    numLots !== plannedLots ||
    entryPrice !== baseEntry ||
    slPrice !== baseSl ||
    tslPrice !== baseTsl ||
    targetPrice !== baseTarget;

  const resetDefaults = () => {
    setNumLots(plannedLots);
    setEntryPrice(baseEntry);
    setSlPrice(baseSl);
    setTslPrice(baseTsl);
    setTargetPrice(baseTarget);
  };

  // Calculations
  const totalQty = Math.max(0, numLots * lotSize);
  const totalInvestment = roundToTick(entryPrice * totalQty) ?? (entryPrice * totalQty);
  const liveLtp = roundToTick(currentLtp ?? entryPrice) ?? entryPrice;

  // Covered Points & Return
  const coveredPoints = roundToTick(liveLtp - entryPrice) ?? 0;
  const coveredPct = entryPrice > 0 ? Number(((coveredPoints / entryPrice) * 100).toFixed(2)) : 0;
  const unrealizedPnl = roundToTick(coveredPoints * totalQty) ?? 0;
  const isProfit = coveredPoints >= 0;

  // Hard SL Risk. A bought option can go to zero, so ORB (hideTsl) sizes
  // against the full premium outlay — the same number the board row shows.
  const slDistance = Math.max(0, roundToTick(entryPrice - slPrice) ?? 0);
  const premiumOutlay = roundToTick(entryPrice * totalQty) ?? (entryPrice * totalQty);
  const maxRiskAmount = hideTsl ? premiumOutlay : (roundToTick(slDistance * totalQty) ?? 0);
  const riskPerLot = hideTsl
    ? (roundToTick(entryPrice * lotSize) ?? (entryPrice * lotSize))
    : (roundToTick(slDistance * lotSize) ?? 0);

  // Trailing Stop Loss (TSL) Locked Profit / Risk Protection
  const tslDistance = roundToTick(tslPrice - entryPrice) ?? 0;
  const tslPnl = roundToTick(tslDistance * totalQty) ?? 0;
  const isRiskFree = tslPrice >= entryPrice;

  // Target / Reward
  const targetDistance = Math.max(0, roundToTick(targetPrice - entryPrice) ?? 0);
  const targetReward = roundToTick(targetDistance * totalQty) ?? 0;
  const riskRewardRatio = slDistance > 0 ? (targetDistance / slDistance).toFixed(2) : '1.00';
  const realizedRR = slDistance > 0 ? (coveredPoints / slDistance).toFixed(2) : '0.00';

  const estFrictionINR = 60 * numLots;
  const isLowExpectancy = targetReward > 0 && targetReward < 240 * numLots;

  const isClosed = useMemo(() => {
    if (!exitState) return false;
    const s = exitState.toUpperCase();
    return (
      s.includes('CLOSED') ||
      s.includes('ENDED') ||
      s.includes('STOP') ||
      s.includes('SL_HIT') ||
      s.includes('TARGET') ||
      s.includes('EXPIRED') ||
      s.includes('EXITED')
    );
  }, [exitState]);

  const isExpiringSoon = useMemo(() => {
    if (!expiry) return false;
    try {
      const expDate = new Date(expiry);
      if (isNaN(expDate.getTime())) return false;
      const now = new Date();
      const diffDays = (expDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
      return diffDays >= -0.5 && diffDays <= 1.2;
    } catch {
      return false;
    }
  }, [expiry]);

  const handlePlaceOrder = () => {
    if (isClosed || !allowOrder || lotSize <= 0 || entryPrice <= 0) return;
    const tradeSymbol = tradingsymbol || symbol;
    const slPercentage =
      entryPrice > 0 && slPrice > 0
        ? -Math.abs(Number((((entryPrice - slPrice) / entryPrice) * 100).toFixed(1)))
        : undefined;
    const tgtPercentage =
      entryPrice > 0 && targetPrice > 0
        ? Math.abs(Number((((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)))
        : undefined;

    openOrderWindow({
      symbol: tradeSymbol,
      exchange,
      initialSide: 'BUY',
      initialQty: totalQty,
      lastPrice: entryPrice,
      lotSize,
      initialSlPct: slPercentage,
      initialTgtPct: tgtPercentage,
      tag,
    });
  };

  const handleCopyTradePlan = () => {
    const tslLine = hideTsl ? '' : `\nTSL: ₹${fmtTick(tslPrice)}`;
    const text = `TRADE PLAN\nSymbol: ${formatInstrument(tradingsymbol || symbol)} (${exchange})\nLots: ${numLots} (${totalQty} Qty)\nEntry: ₹${fmtTick(entryPrice)}\nStop Loss: ₹${fmtTick(slPrice)} (-${fmtTick(slDistance)} pts)${tslLine}\nTarget: ₹${fmtTick(targetPrice)} (+${fmtTick(targetDistance)} pts)\nRisk/Reward: 1 : ${riskRewardRatio} R`;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        fontFamily: k.fontFamily,
      }}
    >
      {/* ── CLOSED SETUP NOTICE BANNER ── */}
      {isClosed && (
        <div
          style={{
            background: '#f8f9fa',
            border: `1px solid ${k.border}`,
            borderRadius: 4,
            padding: '7px 12px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11.5,
            color: k.dim,
          }}
        >
          <span>🔒</span>
          <span><strong>Signal Closed ({exitState}):</strong> This setup has completed its lifecycle. Trade plan is locked for historical review; order execution is disabled.</span>
        </div>
      )}

      {/* ── EXPIRY WARNING BANNER (IF 0-1 DTE) ── */}
      {isExpiringSoon && (
        <div
          style={{
            background: '#fff8e6',
            border: `1px solid ${k.orange}40`,
            borderRadius: 4,
            padding: '6px 12px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 11,
            color: '#b35900',
          }}
        >
          <span>⚠️ <strong>Expiring Soon ({expiry})</strong>: High theta decay & physical delivery risk on stocks. Plan rollout if holding.</span>
          <span style={{ fontSize: 10, background: '#ffe0b2', padding: '1px 6px', borderRadius: 2, fontWeight: 600 }}>0/1 DTE</span>
        </div>
      )}

      {/* ── TOP INPUTS & POSITION SIZING CARD ── */}
      <div
        style={{
          background: 'var(--k-bg)',
          border: `1px solid ${k.border}`,
          borderRadius: 4,
          padding: '12px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {/* Header Row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: k.text, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Position Sizing & Trade Plan
            </span>
            <span
              style={{
                fontSize: 10,
                fontWeight: 500,
                padding: '1px 6px',
                borderRadius: 2,
                background: optionType === 'CE' ? `${k.green}18` : `${k.red}18`,
                color: optionType === 'CE' ? k.green : k.red,
              }}
            >
              {optionType}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {isCustomized && (
              <button
                type="button"
                onClick={resetDefaults}
                style={{
                  fontSize: 11,
                  color: k.blue,
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  padding: 0,
                  textDecoration: 'underline',
                }}
              >
                Reset Defaults
              </button>
            )}
            <span style={{ fontSize: 11, color: k.dim, fontVariantNumeric: 'tabular-nums' }}>
              {totalQty} Qty ({numLots} Lot{numLots > 1 ? 's' : ''} × {lotSize})
            </span>
          </div>
        </div>

        {/* 5-Column Inputs Strip */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
            gap: 8,
          }}
        >
          {/* 1. Lots Stepper */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Lots
            </span>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                height: 26,
                background: 'var(--k-bg)',
              }}
            >
              <button
                type="button"
                onClick={() => setNumLots((l) => Math.max(1, l - 1))}
                style={{
                  width: 24,
                  height: '100%',
                  border: 'none',
                  background: 'transparent',
                  color: numLots <= 1 ? k.dim : k.text,
                  cursor: numLots <= 1 ? 'not-allowed' : 'pointer',
                  fontSize: 13,
                  lineHeight: '26px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                disabled={numLots <= 1}
              >
                −
              </button>
              <input
                type="number"
                min={1}
                max={500}
                value={numLots}
                onChange={(e) => {
                  const val = parseInt(e.target.value, 10);
                  if (!isNaN(val) && val >= 1) setNumLots(val);
                }}
                style={{
                  width: '100%',
                  height: '100%',
                  border: 'none',
                  textAlign: 'center',
                  fontSize: 12,
                  color: k.text,
                  fontVariantNumeric: 'tabular-nums',
                  padding: 0,
                  outline: 'none',
                  MozAppearance: 'textfield',
                }}
              />
              <button
                type="button"
                onClick={() => setNumLots((l) => l + 1)}
                style={{
                  width: 24,
                  height: '100%',
                  border: 'none',
                  background: 'transparent',
                  color: k.text,
                  cursor: 'pointer',
                  fontSize: 13,
                  lineHeight: '26px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                +
              </button>
            </div>
          </div>

          {/* 2. Entry (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Entry (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={entryPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setEntryPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.text,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* 3. SL (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              SL (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={slPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setSlPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.red,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* 4. TSL (₹) — hidden when the engine does not produce a trail (ORB). */}
          {!hideTsl && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              TSL (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={tslPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setTslPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: isRiskFree ? k.green : k.orange,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>
          )}

          {/* 5. Target (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Target (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={targetPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setTargetPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.purple,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>
        </div>
      </div>

      <div className="plan-metrics" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10 }}>
        <MetricCard title="Position estimate" summary={lotSize > 0 ? `${totalQty} Qty` : 'Lot size required'}>
          <Metric label="Entry / capital" value={`₹${fmtTick(entryPrice)} · ${lotSize > 0 ? fmtINR(totalInvestment) : '—'}`} />
          <Metric label="Current LTP" value={currentLtp != null ? `₹${fmtTick(liveLtp)}` : '—'}
            detail={currentLtp != null ? `${coveredPoints >= 0 ? '+' : ''}${fmtTick(coveredPoints)} pts (${coveredPct >= 0 ? '+' : ''}${coveredPct.toFixed(2)}%)` : 'Quote unavailable'} tone={isProfit ? k.green : k.red} />
          <Metric label="Gross MTM estimate" value={currentLtp != null && lotSize > 0 ? fmtINR(unrealizedPnl, { showSign: true }) : '—'}
            detail={currentLtp != null && lotSize > 0 && slDistance > 0 ? `${realizedRR}R` : undefined} tone={isProfit ? k.green : k.red} />
          <span style={{ color: k.dim, fontSize: 10 }}>Plan quantity; confirmed holdings appear in Positions.</span>
        </MetricCard>
        <MetricCard title="Protection" summary="Planned levels">
          <Metric label="Stop (SL)" value={defaultSl != null || isCustomized ? `₹${fmtTick(slPrice)}` : '—'}
            detail={defaultSl != null || isCustomized ? `-${fmtTick(slDistance)} pts · ${lotSize > 0 ? fmtINR(-maxRiskAmount) : '—'}` : 'No strategy stop supplied'} tone={k.red} />
          {hideTsl && <Metric label="Premium at risk" value={lotSize > 0 ? fmtINR(-premiumOutlay) : '—'} tone={k.red} />}
          {!hideTsl && <Metric label="Trail (TSL)" value={defaultTsl != null || isCustomized ? `₹${fmtTick(tslPrice)}` : '—'}
            detail={defaultTsl != null || isCustomized ? `${isRiskFree ? 'Profit locked' : 'Downside'} · ${lotSize > 0 ? fmtINR(tslPnl, { showSign: true }) : '—'}` : 'No strategy trail supplied'} tone={isRiskFree ? k.green : k.orange} />}
          <span style={{ color: k.dim, fontSize: 10 }}>Stop outcomes exclude slippage and charges; verify broker protection.</span>
        </MetricCard>
        <MetricCard title="Exit plan">
          <Metric label="Target / reward" value={targetPrice > entryPrice ? `₹${fmtTick(targetPrice)}` : '—'}
            detail={targetPrice > entryPrice ? `${lotSize > 0 ? fmtINR(targetReward, { showSign: true }) : '—'} · ${riskRewardRatio}R` : 'No fixed target supplied'} tone={k.purple} />
          <Metric label="Estimated charges" value={lotSize > 0 ? `~${fmtINR(estFrictionINR)}` : '—'} detail="Sizing allowance; actual fees vary" />
          <Metric label="Exit status" value={exitState || '—'} tone={exitState?.includes('red') ? k.orange : k.text} />
        </MetricCard>
      </div>

      {/* ── 4. 1-CLICK KITE ORDER EXECUTION BAR ── */}
      <div
        style={{
          background: 'var(--k-bg)',
          border: `1px solid ${k.border}`,
          borderRadius: 4,
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ color: k.dim }}>Selected Contract:</span>
          <strong title={tradingsymbol || symbol} style={{ color: k.text }}>{formatInstrument(tradingsymbol || symbol)}</strong>
          <span style={{ color: k.dim }}>({exchange})</span>
          <span style={{ color: k.dim, marginLeft: 4 }}>·</span>
          <span style={{ color: k.text, fontVariantNumeric: 'tabular-nums' }}>{totalQty} Qty @ ₹{fmtTick(entryPrice)}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            onClick={handleCopyTradePlan}
            style={{
              padding: '6px 12px',
              fontSize: 11.5,
              borderRadius: 3,
              border: `1px solid ${k.border}`,
              background: 'var(--k-bg)',
              color: k.text,
              cursor: 'pointer',
            }}
          >
            {copied ? '✓ Copied Plan' : '📋 Copy Plan'}
          </button>

          {executionAction ?? (isClosed || !allowOrder || lotSize <= 0 ? (
            <div
              style={{
                padding: '6px 14px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 3,
                border: `1px solid ${k.border}`,
                background: '#f1f3f4',
                color: '#70757a',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                cursor: 'not-allowed',
              }}
              title={isClosed
                ? "This trade setup is closed. Order placement is locked."
                : unavailableReason || "This strategy detail is read-only; use its strategy-owned execution path."}
            >
              <span>{isClosed ? 'Setup closed' : lotSize <= 0 ? 'Lot size unavailable' : unavailableReason || 'Quote & sizing preview'}</span>
              {isClosed && (
                <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.85 }}>({exitState || 'Closed'})</span>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={handlePlaceOrder}
              style={{
                padding: '6px 16px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 3,
                border: 'none',
                background: optionType === 'PE' ? 'var(--k-orange)' : 'var(--k-blue)',
                color: 'var(--k-bg)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
              }}
            >
              <span>Place {optionType === 'PE' ? 'BUY PUT (PE)' : 'BUY CALL (CE)'}</span>
              <span style={{ opacity: 0.85, fontSize: 11 }}>({totalQty} Qty)</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default AdaptiveEdgePositionCalculator;

function MetricCard({ title, summary, children }: { title: string; summary?: string; children: React.ReactNode }) {
  return <section style={{ background: k.bg, border: `1px solid ${k.border}`, borderRadius: 6, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: `1px solid ${k.border}`, paddingBottom: 6, fontSize: 11 }}>
      <strong style={{ color: k.text }}>{title}</strong><span style={{ color: k.dim }}>{summary}</span>
    </div>{children}
  </section>;
}
function Metric({ label, value, detail, tone = k.text }: { label: string; value: string; detail?: string; tone?: string }) {
  return <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, alignItems: 'baseline' }}>
    <span style={{ color: k.dim }}>{label}</span><span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone }}>
      <span>{value}</span>{detail && <small style={{ display: 'block', fontSize: 10.5, marginTop: 3 }}>{detail}</small>}
    </span>
  </div>;
}

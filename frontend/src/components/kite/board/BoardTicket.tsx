/**
 * What an expanded row shows, on every board.
 *
 * SuperTrend's expanded leg was the one that got this right, so this is not a
 * lookalike of it — it mounts the same components:
 *
 *   1. AdaptiveEdgePositionCalculator — sizing and P&L
 *   2. QuoteDetail — Buy / Sell, the five-level book, session stats, Greeks
 *
 * The other boards previously showed a depth ladder and a quote-stats card
 * side by side with no way to place an order from them, which is the gap that
 * made them feel like a different product from SuperTrend.
 *
 * Greeks are computed from the live quote rather than taken from the adapter,
 * because a board that has been open for an hour should not be quoting the
 * delta a scan produced an hour ago.
 */
import React from 'react';
import { useKiteQuote } from '../../../hooks/useKite';
import { useOrderWindowStore } from '../../../store/useOrderWindowStore';
import { QuoteDetail } from '../SterlingWatchList';
import { InstrumentLabel } from '../InstrumentLabel';
import { AdaptiveEdgePositionCalculator } from '../AdaptiveEdgePositionCalculator';
import { computeGreeksFromLeg } from '../../../utils/computeGreeks';
import { roundToTick } from '../../../utils/fmt';
import type { BoardSignal } from './boardTypes';

/**
 * Stop and target as a percentage of the working price.
 *
 * The order window takes percentages, not levels, so a Buy raised from here
 * arrives with the signal's own risk already filled in rather than blank.
 * Returns undefined when either end is missing — a bracket derived from a
 * guess is worse than no bracket.
 */
function bracketPct(from: number | null, level: number | null): number | undefined {
  if (!from || from <= 0 || !level || level <= 0) return undefined;
  return Number((((level - from) / from) * 100).toFixed(1));
}

export function BoardTicket({ signal, tag, allowTrade = true, executionAction }: {
  signal: BoardSignal;
  /** Marks the order's origin in the broker record. */
  tag?: string;
  /** False for strategies whose exposure must go through a strategy-owned endpoint. */
  allowTrade?: boolean;
  executionAction?: React.ReactNode;
}) {
  const key = signal.instrument.quoteKey;
  const historical = signal.historical === true;
  // 'full' carries the five-level book. Fetched per contract on expand, never
  // for the whole universe, so opening a row does not subscribe everything.
  const { data } = useKiteQuote(key && !historical ? [key] : [], !!key && !historical, 5_000, 'full');
  const quote = key && !historical ? (data as Record<string, any> | undefined)?.[key] : undefined;
  const openOrderWindow = useOrderWindowStore((s) => s.openOrderWindow);

  const observed = quote?.last_price ?? signal.levels.ltp;
  const live = typeof observed === 'number' && Number.isFinite(observed) && observed > 0 ? observed : null;
  const entry = signal.levels.entry;
  const entryKnown = typeof entry === 'number' && Number.isFinite(entry) && entry > 0;
  const difference = live != null && entryKnown ? (live-entry)/entry*100 : null;
  const stop = signal.levels.stop;
  const atOrBelowStop = live != null && typeof stop === 'number' && Number.isFinite(stop) && stop > 0 && live <= stop;
  const { strike, expiry, optionType, lotSize, symbol, exchange } = signal.instrument;

  // The underlying's price, which the Greeks need. Adapters put it on the
  // parent's spot section; falling back to the premium would be nonsense, so
  // when it is absent the Greeks are simply not computed.
  const spot = React.useMemo(() => {
    for (const section of signal.sections) {
      for (const stat of section.stats) {
        if (!/^spot( entry)?$/i.test(stat.label)) continue;
        const n = Number(String(stat.value ?? '').replace(/[^\d.-]/g, ''));
        if (Number.isFinite(n) && n > 0) return n;
      }
    }
    return null;
  }, [signal.sections]);

  const greeks = React.useMemo(() => {
    if (strike == null || !expiry || !optionType || !spot) return null;
    return computeGreeksFromLeg(strike, expiry, optionType, spot, quote, lotSize ?? null);
  }, [strike, expiry, optionType, spot, quote, lotSize]);

  const genericTradeAllowed = allowTrade && !historical && signal.executionEligible !== false && signal.engine !== 'snapback' && signal.engine !== 'gamma_move' && signal.engine !== 'ema_trend' && signal.engine !== 'adaptive_edge';
  const buyAllowed = genericTradeAllowed && live != null && !atOrBelowStop && (signal.status === 'armed' || (signal.engine === 'supertrend' && signal.status === 'running')) && (lotSize ?? 0) > 0;
  const sellAllowed = false; // Exits use confirmed owned holdings from Positions.
  // A planned row opens its own ticket: qty and SL from the plan, priced off
  // the PLAN entry so a live premium move cannot silently resize the risk. A
  // trade taken at 1:2 on the plan becomes 1:0.8 on a tick that ran away.
  const planPx = (signal.planPriced ? signal.levels.entry : null) || live || 0;
  const raiseOrder = (side: 'BUY' | 'SELL') => openOrderWindow({
    symbol,
    exchange,
    initialSide: side,
    fromSignal: true,
    planStop: signal.levels.stop ?? signal.levels.trail ?? null,
    lotSize: lotSize || 1,
    initialQty: signal.sizing.quantity ?? undefined,
    lastPrice: planPx,
    // Bracket percentages off the PLAN entry on a planned row, so the stop
    // stays the distance the strategy chose rather than a distance from
    // wherever the premium happens to be now.
    initialSlPct: bracketPct(
      signal.planPriced ? (signal.levels.entry ?? live) : live, signal.levels.stop),
    initialTgtPct: bracketPct(
      signal.planPriced ? (signal.levels.entry ?? live) : live, signal.levels.target),
    tag,
  });

  return (
    <>
      {!historical && <div style={{padding: '8px 10px', fontSize: 12, lineHeight: 1.6, color: 'var(--k-text)', background: 'var(--k-surface)', border: '1px solid var(--k-border)', borderRadius: 4}}>
        <div>{difference == null ? 'Observed premium vs plan: unavailable. A current premium and a valid plan entry are required.'
          : `Observed premium vs plan: ₹${live!.toFixed(2)} vs ₹${entry!.toFixed(2)} (${difference >= 0 ? '+' : ''}${difference.toFixed(2)}%).`}</div>
        <div style={{color: 'var(--k-dim)', fontSize: 11}}>This comparison does not change the plan or set a price tolerance. The server rechecks quote freshness, the exact plan and risk before entry.</div>
        {atOrBelowStop && <div role="alert" style={{color: 'var(--k-text)', borderLeft: '2px solid var(--k-amber)', paddingLeft: 6}}>The observed premium is at or below the plan stop. Refresh the plan before a new entry.</div>}
      </div>}
      <AdaptiveEdgePositionCalculator
        key={signal.id}
        symbol={symbol}
        tradingsymbol={symbol}
        exchange={exchange}
        expiry={expiry ?? undefined}
        lotSize={lotSize ?? undefined}
        defaultLots={signal.sizing.lots ?? undefined}
        defaultEntryPrice={roundToTick(signal.levels.entry) ?? undefined}
        defaultSl={roundToTick(signal.levels.stop) ?? undefined}
        defaultTsl={roundToTick(signal.levels.trail) ?? undefined}
        defaultExit={roundToTick(signal.levels.target) ?? undefined}
        currentLtp={roundToTick(live) ?? undefined}
        optionType={(optionType ?? 'CE') as 'CE' | 'PE'}
        exitState={signal.status === 'ended' ? 'EXIT' : signal.exitProgress || 'HOLD'}
        tag={tag}
        signalPlanStop={signal.levels.stop ?? signal.levels.trail ?? null}
        hideTsl={signal.noTrailingStop === true}
        allowOrder={buyAllowed}
        executionAction={executionAction}
        unavailableReason={historical ? 'Historical signals are read-only.'
          : atOrBelowStop ? 'The observed premium is at or below the plan stop.'
          : live == null ? 'A current premium could not be verified.'
          : signal.executionEligible === false && signal.reason ? signal.reason
          : signal.status !== 'armed' ? 'A fresh armed entry signal is required.'
          : 'Use the strategy-owned Buy action.'}
      />

      <QuoteDetail
        sym={key ?? undefined}
        q={quote}
        expiry={expiry ?? undefined}
        spotName={signal.underlying}
        spotPx={spot ?? undefined}
        instrumentName={<InstrumentLabel symbol={symbol} />}
        greeks={greeks ?? undefined}
        // Strategy exits use broker-confirmed holdings in Positions.
        onBuy={buyAllowed ? () => raiseOrder('BUY') : undefined}
        onSell={sellAllowed ? () => raiseOrder('SELL') : undefined}
      />
    </>
  );
}

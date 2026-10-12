import { useMemo } from 'react';
import { useKiteQuote } from '../../../hooks/useKite';
import { useKiteSettings } from '../../../store/useKiteSettings';
import type { BoardSignal } from './boardTypes';
import { dayMoveFromQuote } from './supertrendAdapter';
import { computeGreeksFromLeg, underlyingSpotKey } from '../../../utils/computeGreeks';

/** Quote enrichment is display-only: never changes a plan or its entry authority. */
export function enrichSignalMarketData(
  signals: readonly BoardSignal[], quotes: Record<string, any>, basis: 'close' | 'open',
): BoardSignal[] {
  return signals.map(signal => {
    // Retained history and replay must keep their own prices and clock.
    if (signal.historical) return signal;
    const quote = signal.instrument.quoteKey ? quotes[signal.instrument.quoteKey] : undefined;
    if (!quote) return signal;
    const last = quote.last_price;
    const instrument = signal.instrument;
    const spotKey = underlyingSpotKey(instrument.symbol);
    const spot = spotKey ? quotes[spotKey]?.last_price : undefined;
    const greeks = typeof spot === 'number' && Number.isFinite(spot) && spot > 0
      && instrument.strike != null && instrument.expiry && instrument.optionType
      ? computeGreeksFromLeg(instrument.strike, instrument.expiry, instrument.optionType, spot, quote, instrument.lotSize ?? null)
      : null;
    return {
      ...signal,
      levels: { ...signal.levels, ltp: typeof last === 'number' && Number.isFinite(last) && last > 0
        ? last : signal.levels.ltp },
      dayMove: dayMoveFromQuote(quote, basis) ?? signal.dayMove,
      delta: greeks && Number.isFinite(greeks.iv) && greeks.iv > 0 ? greeks.delta : signal.delta,
    };
  });
}

export function useSignalMarketData(signals: readonly BoardSignal[], historical: boolean) {
  const basis = useKiteSettings(s => s.chgType);
  const keys = useMemo(() => historical ? [] : [...new Set(signals
    .filter(s => !s.historical)
    .flatMap(s => [s.instrument.quoteKey, underlyingSpotKey(s.instrument.symbol)])
    .filter((key): key is string => !!key))], [signals, historical]);
  // Empty keys also prevent WS subscriptions, rather than only disabling REST.
  const { data: quotes } = useKiteQuote(keys, !historical);
  return useMemo(() => historical ? [...signals]
    : enrichSignalMarketData(signals, quotes ?? {}, basis), [signals, quotes, basis, historical]);
}

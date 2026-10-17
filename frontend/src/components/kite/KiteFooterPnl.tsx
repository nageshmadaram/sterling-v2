import React, { useMemo } from 'react';
import { useKiteLtp, useKitePositions } from '../../hooks/useKite';
import { k } from '../../styles/kiteUI';

/** The tick and broker-position subscriptions belong to this tiny footer chip,
 * not to KiteLayout. Otherwise every price update re-renders the entire
 * workspace and its multi-dock tree.
 *
 * This is informational only: order risk and reconciliation never use it.
 */
export const KiteFooterPnl = React.memo(function KiteFooterPnl({
  onOpenPositions,
}: { onOpenPositions: () => void }) {
  // The broker's positions remain the inventory authority. Polling the footer
  // every second served no execution purpose; the positions pane remains free
  // to subscribe more frequently for its own needs.
  const positionsQuery = useKitePositions(true, 5_000);
  const positions = positionsQuery.data?.net;
  const symbols = useMemo(
    () => Array.from(new Set((positions ?? []).map(
      (p: any) => `${p.exchange}:${p.tradingsymbol}`))),
    [positions],
  );
  // WebSocket ticks still update live P&L; REST is a fallback/resolver instead
  // of a mandatory 1-second call from the always-mounted workspace.
  const { data: ltp } = useKiteLtp(symbols, symbols.length > 0, 30_000);
  const pnl = useMemo(() => (positions ?? []).reduce((sum: number, p: any) => {
    const brokerPnl = Number(p.pnl ?? p.m2m ?? 0);
    const live = Number(ltp?.[`${p.exchange}:${p.tradingsymbol}`]?.last_price ?? 0);
    const brokerLast = Number(p.last_price ?? 0);
    const qty = Number(p.quantity ?? 0);
    const multiplier = Number(p.multiplier ?? 1) || 1;
    return sum + (live > 0 && brokerLast > 0 && qty !== 0
      ? brokerPnl + (live - brokerLast) * qty * multiplier : brokerPnl);
  }, 0), [positions, ltp]);
  const openCount = (positions ?? []).filter((p: any) => Number(p.quantity ?? 0) !== 0).length;
  if (!positions?.length || (openCount === 0 && pnl === 0)) return null;
  const amount = Math.abs(pnl).toLocaleString('en-IN', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const text = `${pnl > 0 ? '+' : pnl < 0 ? '−' : ''}₹${amount}`;
  return <>
    <span aria-hidden="true" style={{ width: 1, height: 14, background: 'var(--k-border)' }} />
    <button type="button" onClick={onOpenPositions}
      title={`Total P&L: ${text} across ${positions.length} position${positions.length === 1 ? '' : 's'} (${openCount} open). Click to view positions.`}
      className="sb-tool"
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, height: 20,
        padding: '0 6px', borderRadius: 4, whiteSpace: 'nowrap',
        background: pnl > 0 ? 'var(--k-tint-green, rgba(34, 197, 94, 0.1))'
          : pnl < 0 ? 'var(--k-tint-red, rgba(239, 68, 68, 0.1))' : 'var(--k-surface-hover)',
        border: `1px solid ${pnl > 0 ? 'rgba(34, 197, 94, 0.3)'
          : pnl < 0 ? 'rgba(239, 68, 68, 0.3)' : 'var(--k-border)'}`,
        color: pnl > 0 ? k.green : pnl < 0 ? k.red : 'var(--k-dim)',
        fontFamily: 'inherit', fontSize: 9.5, fontWeight: 800,
        letterSpacing: '.03em', cursor: 'pointer',
      }}>
      <span style={{ fontSize: 8.5, color: 'var(--k-dim)', fontWeight: 700 }}>P&amp;L</span>
      <span>{text}</span>
    </button>
  </>;
});

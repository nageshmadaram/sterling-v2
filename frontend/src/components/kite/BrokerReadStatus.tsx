import React from 'react';

/** A failed broker read never establishes empty inventory. Keep cached rows visible. */
export function BrokerReadStatus({label, error, hasData, retry, isFetching = false}: {
  label: 'orders' | 'positions' | 'holdings'; error: unknown; hasData: boolean;
  retry: () => unknown; isFetching?: boolean;
}) {
  if (!error && hasData) return null;
  if (!error) return <p role="status" style={{color:'var(--k-dim)',fontSize:13}}>Loading broker {label}…</p>;
  return <div role="alert" style={{padding:'12px 14px',margin:'12px 0',border:'1px solid var(--k-amber-2)',borderRadius:4,color:'var(--k-text)',fontSize:13,lineHeight:1.5}}>
    <strong>Broker {label} unavailable.</strong> Current {label === 'positions' ? 'exposure is' : `${label} are`} unknown.
    {hasData && <span> Showing the last known snapshot; quantities, status and P&amp;L may be stale.</span>}
    <button type="button" disabled={isFetching} aria-busy={isFetching} onClick={()=>void retry()}
      style={{marginLeft:12,border:0,background:'transparent',color:'var(--k-blue-kite)',cursor:isFetching?'wait':'pointer',font:'inherit'}}>Retry {label}</button>
  </div>;
}

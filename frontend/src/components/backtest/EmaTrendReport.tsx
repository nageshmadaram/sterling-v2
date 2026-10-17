import React from 'react';
import type { EmaTrendReplayReport } from '../../types/emaTrend';
import { EMA_TREND_MODE_LABEL } from '../../types/emaTrend';
import { fmtMoney, fmtPx } from '../../lib/format';

/** Native campaign accounting: partial fills are legs of a campaign, not independent wins. */
export function EmaTrendReport({ report }: { report: EmaTrendReplayReport }) {
  const n = (value: unknown) => value == null ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  return <section aria-label="EMA Trend lifecycle report" style={{ padding: 12, fontSize: 12 }}>
    <h3>{report.symbol} · {EMA_TREND_MODE_LABEL[report.mode]} · historical evidence</h3>
    <p><strong>{report.evidence_status}</strong> · Live eligibility: {report.live_eligible ? 'Reported by server' : 'unavailable'}. Modelled historical fills do not prove executable profits.</p>
    {report.contract && <p>{report.contract.execution_vehicle} · {report.contract.product} · lot {report.contract.lot_size}{report.contract.expiry ? ` · expiry ${report.contract.expiry}` : ''} · {report.contract.capital_model.replace(/_/g, ' ')}</p>}
    <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(145px,1fr))', gap: 8 }}>
      {[
        ['Completed campaigns', report.metrics.completed_campaigns], ['Open at dataset end', report.metrics.open_campaigns],
        ['Net realized', fmtMoney(n(report.metrics.net_realized))], ['Estimated fees', fmtMoney(n(report.metrics.estimated_fees))],
        ['Maximum drawdown', fmtMoney(n(report.metrics.max_drawdown))], ['Ending equity', fmtMoney(n(report.metrics.ending_equity))],
      ].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd style={{ margin: 0, fontWeight: 650 }}>{value}</dd></div>)}
    </dl>
    <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', textAlign: 'left' }}>
      <caption>Campaigns · confirmed quantities</caption><thead><tr><th>Campaign</th><th>State</th><th>Initial units</th><th>Remaining</th><th>Realized net</th><th>Fees</th></tr></thead>
      <tbody>{report.campaigns.map(c => <tr key={c.id}><td>{c.id.slice(0, 12)}</td><td>{c.state}</td><td>{c.q0 ?? '—'}</td><td>{c.quantity ?? c.qty ?? '—'}</td><td>{fmtMoney(n(c.realized_net))}</td><td>{fmtMoney(n(c.total_fees))}</td></tr>)}</tbody>
    </table></div>
    <div style={{ overflowX: 'auto', marginTop: 12 }}><table style={{ width: '100%', textAlign: 'left' }}>
      <caption>Fill legs · read-only</caption><thead><tr><th>Campaign</th><th>Side</th><th>Units</th><th>Price</th><th>Estimated fee</th></tr></thead>
      <tbody>{report.fills.map(fill => <tr key={fill.id}><td>{fill.campaign_id.slice(0, 12)}</td><td>{fill.side}</td><td>{fill.quantity ?? fill.qty ?? '—'}</td><td>{fmtPx(n(fill.price))}</td><td>{fmtMoney(n(fill.fees ?? fill.fee))}</td></tr>)}</tbody>
    </table></div>
    {report.limitations.map(reason => <p key={reason}>{reason.replace(/_/g, ' ')}</p>)}
    {report.dataset_manifest && <section aria-label="Recorded dataset quality">
      <strong>Recorded dataset quality</strong>
      {(['signal', 'context'] as const).map(role => { const audit = report.dataset_manifest![role]; return <div key={role}>
        {role}: {audit.bar_count} bars · {audit.quality_status.replace(/_/g, ' ')}
        {audit.issues.map(issue => <p key={issue.code}>{issue.code.replace(/_/g, ' ')}: {issue.count}</p>)}
      </div>; })}
      <p>Checks cover the recorded bounds. They do not certify corporate actions, full provider coverage or executable fills.</p>
      <details><summary>Dataset manifest</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(report.dataset_manifest, null, 2)}</pre></details>
    </section>}
    <details><summary>Evidence identity and fill model</summary><p>Dataset: {report.dataset_hash}<br />Rules: {report.rule_hash}<br />Configuration: {report.config_hash}<br />{report.fill_model}</p></details>
  </section>;
}

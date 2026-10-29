/** Phase 8 Sterling Offline Evidence Center — observability only.
 * This component NEVER imports broker, order, mutation or live-execution hooks.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import {
  OBSERVABILITY_ROOT, evidenceIsReadOnly,
  type Catalogue, type OfflineStatus, type OfflineSummary,
  type DecisionEnvelope, type Reconciliation, type Breakdown,
} from './contracts';
import './OfflineEvidencePanel.css';

const AGE_LIMIT_MS = 90_000;
const POLL_MS = 30_000;
const request = <T,>(path: string) => api.get<T>(OBSERVABILITY_ROOT + path);

function metric(value: string | number | null | undefined, kind: 'inr' | 'percent' | 'ratio' = 'ratio') {
  if (value == null || value === '') return 'Unavailable';
  const n = Number(value);
  if (!Number.isFinite(n)) return 'Unavailable';
  const shown = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(n);
  return kind === 'inr' ? `₹${shown}` : kind === 'percent' ? `${shown}%` : shown;
}

function shortHash(hash: string | null | undefined) {
  return typeof hash === 'string' && hash.length > 16
    ? `${hash.slice(0, 10)}…${hash.slice(-8)}` : (hash || 'Unavailable');
}

function timeLabel(ms: number | string | null | undefined) {
  if (ms == null) return 'Unknown time';
  const time = new Date(typeof ms === 'number' ? ms : ms).getTime();
  return Number.isFinite(time) ? new Date(time).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) + ' IST' : 'Unknown time';
}

function Panel({ title, children, testid }: { title: string; children: ReactNode; testid?: string }) {
  return <section className="oe-card" aria-label={title} data-testid={testid}>
    <h2>{title}</h2>{children}
  </section>;
}

function StatusChip({ text, tone = 'neutral' }: { text: string; tone?: 'neutral' | 'warning' | 'critical' }) {
  return <span className={`oe-chip oe-chip--${tone}`}>{text}</span>;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return <div className="oe-stat"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>;
}

function Curve({ points }: { points: OfflineSummary['equity_curve'] }) {
  const valid = points.filter(row => Number.isFinite(Number(row.equity_inr)));
  if (!valid.length) return <p className="oe-muted">No validated offline hypothetical trade curve.</p>;
  if (valid.length === 1) return <p className="oe-muted">One hypothetical point; a time-series trend is unavailable.</p>;
  const numbers = valid.map(p => Number(p.equity_inr));
  const low = Math.min(...numbers);
  const high = Math.max(...numbers);
  const delta = Math.max(high - low, 1);
  const path = numbers.map((v, i) =>
    `${i ? 'L' : 'M'} ${(i / (numbers.length - 1) * 560).toFixed(1)} ${(130 - ((v - low) / delta) * 112).toFixed(1)}`
  ).join(' ');
  return <div className="oe-chart">
    <svg viewBox="0 0 560 140" role="img" aria-label="Hypothetical equity curve, not live account equity" preserveAspectRatio="none">
      <path d="M0 130 H560" stroke="var(--k-border)" strokeWidth="1" fill="none" />
      <path d={path} stroke="var(--k-brand)" strokeWidth="2.5" strokeLinejoin="round" fill="none" />
    </svg>
    <div className="oe-chart-foot"><span>{timeLabel(valid[0].at_utc)}</span><span>{timeLabel(valid[valid.length - 1].at_utc)}</span></div>
  </div>;
}

type Warning = { id: string; text: string; severity: 'info' | 'warning' | 'critical' };
function warnings(summary: OfflineSummary, reconciliation: Reconciliation, status: OfflineStatus, decisions: DecisionEnvelope): Warning[] {
  const out: Warning[] = [];
  if (reconciliation.status === 'untrusted') {
    out.push({ id: 'audit-untrusted', text: 'Audit evidence is untrusted; do not use these metrics as decisions.', severity: 'critical' });
  }
  for (const code of reconciliation.issues) out.push({
    id: 'issue-' + code, text: `Queue reconciliation: ${code}`, severity: code.includes('AUDIT') ? 'critical' : 'warning',
  });
  for (const row of reconciliation.items) {
    for (const code of row.issues) if (!out.some(a => a.id === row.item_id + code))
      out.push({ id: row.item_id + code, text: `${code} · ${row.signal_id}`, severity: 'warning' });
  }
  if (['emergency_stop'].includes(status.offline_gate.system_state) || status.offline_gate.strategy_state === 'emergency_stop')
    out.push({ id: 'stop', text: 'Offline validation gate: EMERGENCY STOP.', severity: 'critical' });
  if (status.strategy_health.score == null)
    out.push({ id: 'health', text: 'Strategy health score is not independently attested.', severity: 'info' });
  for (const row of summary.kpi_alerts) out.push({
    id: 'kpi-' + row.code, text: `Hypothetical KPI warning: ${row.code.replace(/_/g, ' ')}`,
    severity: row.severity === 'info' ? 'info' : 'warning',
  });
  if (decisions.items.some(item => item.data_quality.status === 'degraded'))
    out.push({ id: 'quality', text: 'One or more offline decisions have degraded market-data quality.', severity: 'warning' });
  return out.sort((a, b) => ({ critical: 0, warning: 1, info: 2 })[a.severity] -
                               ({ critical: 0, warning: 1, info: 2 })[b.severity]);
}

function Contribution({ title, groups }: { title: string; groups: Breakdown['groups'] }) {
  const rows = Object.entries(groups || {});
  return <Panel title={title}>
    {!rows.length ? <p className="oe-muted">No hypothetical trade outcomes for this breakdown.</p> :
      <div className="oe-scroll"><table className="oe-table"><thead><tr>
        <th scope="col">Group</th><th scope="col">Trades</th><th scope="col">Net P&amp;L</th><th scope="col">Win rate</th>
      </tr></thead><tbody>{rows.map(([key, data]) => <tr key={key}>
        <td>{key}</td><td>{data.trade_count}</td><td>{metric(data.net_pnl_inr, 'inr')}</td>
        <td>{metric(data.win_rate_pct, 'percent')}</td>
      </tr>)}</tbody></table></div>}
  </Panel>;
}

export function OfflineEvidencePanel() {
  const client = useQueryClient();
  const [selected, setSelected] = useState('');
  const [section, setSection] = useState<'overview' | 'decisions' | 'queue'>('overview');
  const catalog = useQuery({ queryKey: ['offline-evidence', 'catalog'], queryFn: () => request<Catalogue>('/strategies'),
    retry: false, refetchInterval: POLL_MS });
  const strategies = catalog.data?.strategies || [];
  useEffect(() => {
    if (!catalog.data || catalog.isError) return;
    if (!strategies.some(s => s.id === selected)) setSelected(strategies[0]?.id || '');
  }, [catalog.data, catalog.isError, selected, strategies]);
  const enabled = Boolean(selected && strategies.some(s => s.id === selected));
  const base = `/strategies/${encodeURIComponent(selected)}`;
  const status = useQuery({ queryKey: ['offline-evidence', selected, 'status'], queryFn: () => request<OfflineStatus>(base + '/status'), enabled, retry: false, refetchInterval: POLL_MS });
  const summary = useQuery({ queryKey: ['offline-evidence', selected, 'summary'], queryFn: () => request<OfflineSummary>('/analytics' + base + '/summary'), enabled, retry: false, refetchInterval: POLL_MS });
  const decisions = useQuery({ queryKey: ['offline-evidence', selected, 'decisions'], queryFn: () => request<DecisionEnvelope>(base + '/decisions?limit=100'), enabled, retry: false, refetchInterval: POLL_MS });
  const reconciliation = useQuery({ queryKey: ['offline-evidence', selected, 'reconciliation'], queryFn: () => request<Reconciliation>(base + '/reconciliation?limit=100'), enabled, retry: false, refetchInterval: POLL_MS });
  const versions = useQuery({ queryKey: ['offline-evidence', selected, 'versions'], queryFn: () => request<Breakdown>('/analytics' + base + '/comparison?group_by=version'), enabled, retry: false, refetchInterval: POLL_MS });
  const symbols = useQuery({ queryKey: ['offline-evidence', selected, 'symbols'], queryFn: () => request<Breakdown>('/analytics' + base + '/symbols?group_by=symbol'), enabled, retry: false, refetchInterval: POLL_MS });
  const all = [catalog, status, summary, decisions, reconciliation, versions, symbols];
  const loading = all.some(query => query.isPending || query.isFetching && !query.data);
  const failed = all.some(query => query.isError);
  const stale = all.some(query => query.dataUpdatedAt > 0 && Date.now() - query.dataUpdatedAt > AGE_LIMIT_MS);
  const valid = evidenceIsReadOnly(catalog.data, status.data, summary.data, decisions.data,
    reconciliation.data, versions.data, symbols.data) &&
    status.data?.registry.id === selected && catalog.data?.strategies.some(s => s.id === selected);
  const safe = Boolean(enabled && !loading && !failed && !stale && valid &&
    reconciliation.data?.status !== 'untrusted');

  const refresh = () => { void client.invalidateQueries({ queryKey: ['offline-evidence'] }); };
  return <main className="oe-root" data-testid="offline-evidence">
    <header className="oe-header">
      <div><p className="oe-eyebrow">STERLING / OPERATOR OBSERVABILITY</p>
        <h1>Offline strategy evidence</h1>
        <p>Historical validation decisions and hypothetical analytics. No trading actions.</p>
      </div>
      <div className="oe-head-actions">
        <StatusChip tone="critical" text="LIVE EXECUTION NOT AVAILABLE HERE" />
        <span className="oe-muted">Read only</span>
        <button type="button" className="oe-button" onClick={refresh}>Refresh evidence</button>
      </div>
    </header>
    <div className="oe-disclaimer" role="note"><strong>Not proof of profitability.</strong> All P&amp;L values are operator-supplied hypothetical scenarios—not broker fills or account exposure. A passed risk gate is not authorization to trade.</div>
    <div className="oe-toolbar">
      <label htmlFor="oe-strategy">Strategy</label>
      <select id="oe-strategy" value={selected} onChange={e => { setSelected(e.target.value); setSection('overview'); }} disabled={!strategies.length || catalog.isError}>
        {!strategies.length && <option value="">No offline strategies</option>}
        {strategies.map(s => <option key={s.id} value={s.id}>{s.name} · {s.state}</option>)}
      </select>
      <span className="oe-muted">Source: signed, owner-scoped offline journals</span>
    </div>
    {catalog.isPending ? <div role="status" className="oe-status">Loading offline strategies…</div> :
     catalog.isError ? <div role="alert" className="oe-error">Strategy catalogue unavailable. Evidence withheld; retry when authenticated.</div> :
     !strategies.length ? <div role="status" className="oe-status">No versioned offline strategies are registered for this operator. No live data is shown.</div> :
     loading ? <div role="status" className="oe-status">Verifying strategy, analytics and audit chain…</div> :
     !safe ? <div role="alert" className="oe-error" data-testid="offline-fail-closed">
       {reconciliation.data?.status === 'untrusted' ? 'AUDIT UNTRUSTED' : stale ? 'EVIDENCE STALE' :
        failed ? 'EVIDENCE UNAVAILABLE' : 'EVIDENCE CONTRACT INVALID'} — metrics and decisions are withheld. No execution permission is inferred.
       <div className="oe-muted">Refresh or verify the signed session, offline data source, and journal integrity.</div>
     </div> : <OfflineContent key={selected} summary={summary.data!} status={status.data!}
          decisions={decisions.data!} reconciliation={reconciliation.data!}
          versions={versions.data!} symbols={symbols.data!} section={section} setSection={setSection}
          observedAt={Math.min(...all.map(q => q.dataUpdatedAt))} />}
  </main>;
}

function OfflineContent({ summary, status, decisions, reconciliation, versions, symbols, section, setSection, observedAt }: {
  summary: OfflineSummary; status: OfflineStatus; decisions: DecisionEnvelope;
  reconciliation: Reconciliation; versions: Breakdown; symbols: Breakdown;
  section: 'overview' | 'decisions' | 'queue';
  setSection: (s: 'overview' | 'decisions' | 'queue') => void;
  observedAt: number;
}) {
  const alerts = warnings(summary, reconciliation, status, decisions);
  return <>
    <div className="oe-source-line" role="status">
      <span>Offline validation only · strategy {status.registry.state} · {status.registry.locked ? 'locked' : 'unlocked'}</span>
      <span>Last verified read: {timeLabel(observedAt)} · {summary.trade_count} hypothetical trades</span>
    </div>
    <div className="oe-stats" data-testid="offline-kpis">
      <Stat label="Hypothetical net P&L" value={metric(summary.net_pnl_inr, 'inr')} note="Never real broker P&L" />
      <Stat label="Win rate" value={metric(summary.win_rate_pct, 'percent')} />
      <Stat label="Profit factor" value={metric(summary.profit_factor)} />
      <Stat label="Expectancy / trade" value={metric(summary.expectancy_inr, 'inr')} />
      <Stat label="Max drawdown" value={metric(summary.max_drawdown_inr, 'inr')} note={metric(summary.max_drawdown_pct, 'percent')} />
      <Stat label="Sharpe (active days)" value={metric(summary.sharpe_active_utc_days)} note="Synthetic / not broker validated" />
    </div>
    <div className="oe-tabs" role="tablist" aria-label="Offline evidence sections">
      {(['overview','decisions','queue'] as const).map(s =>
        <button key={s} type="button" role="tab" aria-selected={section === s} className={section === s ? 'is-active' : ''} onClick={() => setSection(s)}>
          {s === 'overview' ? 'Performance & risk' : s === 'decisions' ? 'Decision journal' : 'Queue & scheduling'}
        </button>)}
    </div>
    {section === 'overview' && <>
      <div className="oe-grid">
        <Panel title="Hypothetical equity curve" testid="offline-equity"><Curve points={summary.equity_curve} /></Panel>
        <Panel title="Risk & strategy health" testid="offline-risk">
          <dl className="oe-dl">
            <div><dt>System offline gate</dt><dd>{status.offline_gate.system_state}</dd></div>
            <div><dt>Strategy offline gate</dt><dd>{status.offline_gate.strategy_state}</dd></div>
            <div><dt>Approval policy</dt><dd>{status.offline_gate.strategy_approval_mode}</dd></div>
            <div><dt>Strategy health score</dt><dd>Not independently attested</dd></div>
            <div><dt>Strategy locked</dt><dd>{status.registry.locked ? 'Yes' : 'No'}</dd></div>
            <div><dt>Sortino (active days)</dt><dd>{metric(summary.sortino_active_utc_days)}</dd></div>
            <div><dt>Average R multiple</dt><dd>{metric(summary.average_r_multiple)}</dd></div>
            <div><dt>Live broker exposure</dt><dd>Unavailable / not collected</dd></div>
          </dl>
          <p className="oe-muted">Offline approval is a validation result, not trading readiness.</p>
        </Panel>
      </div>
      <div className="oe-grid">
        <Contribution title="Version comparison" groups={versions.groups} />
        <Contribution title="Symbol contribution" groups={symbols.groups} />
      </div>
      <Panel title="Rolling metrics">
        {summary.equity_curve.length ? <div className="oe-scroll"><table className="oe-table"><thead><tr>
          <th>Date</th><th>Equity (hypothetical)</th><th>Drawdown</th><th>Rolling profit factor</th><th>Rolling expectancy</th>
        </tr></thead><tbody>{summary.equity_curve.slice(-12).map(row => <tr key={row.trade_id}>
          <td>{timeLabel(row.at_utc)}</td><td>{metric(row.equity_inr,'inr')}</td><td>{metric(row.drawdown_inr,'inr')}</td>
          <td>{metric(row.rolling_profit_factor)}</td><td>{metric(row.rolling_expectancy_inr,'inr')}</td>
        </tr>)}</tbody></table></div> : <p className="oe-muted">No rolling metrics until sufficient hypothetical trades exist.</p>}
      </Panel>
    </>}
    {section === 'decisions' && <Panel title="Verified offline decision trail" testid="offline-decisions">
      {decisions.items.length === 0 ? <p className="oe-muted">No audited offline decisions recorded.</p> :
      <ol className="oe-decisions">{[...decisions.items].reverse().slice(0, 60).map(d =>
        <li key={d.journal_event_id} className="oe-decision">
          <div><strong>{d.decision_type.replace(/_/g, ' ')}</strong> <StatusChip text={d.approval_status} />
            <span className="oe-muted">{timeLabel(d.at_ms)}</span></div>
          <p>{d.reason}</p>
          <div className="oe-muted">Risk: {d.risk_state} · Approval: {d.approval_mode} · Data: {d.data_quality.status} · Queue: {d.queue_state}</div>
          <details><summary>Audit and provenance</summary>
            <dl className="oe-dl">
              <div><dt>SHA256 audit reference</dt><dd className="oe-hash">{d.audit_ref}</dd></div>
              <div><dt>Risk event</dt><dd className="oe-hash">{d.risk_event_id || 'Unavailable'}</dd></div>
              <div><dt>Approval event</dt><dd className="oe-hash">{d.approval_event_id || 'Unavailable'}</dd></div>
              <div><dt>Lineage</dt><dd className="oe-hash">{d.lineage_sha256}</dd></div>
            </dl>
          </details>
        </li>)}</ol>}
    </Panel>}
    {section === 'queue' && <Panel title="Offline backlog and reconciliation" testid="offline-queue">
      <p className="oe-muted">Source: immutable Phase 5 queue and Phase 6 decision journal. No scheduling, requeue or execution controls.</p>
      <div>Audit status: <StatusChip text={reconciliation.status} tone={reconciliation.status === 'degraded' ? 'warning' : 'neutral'} /></div>
      {reconciliation.items.length === 0 ? <p className="oe-muted">No offline queue records.</p> :
      <div className="oe-scroll"><table className="oe-table"><thead><tr>
        <th>Signal</th><th>Queue state</th><th>Outcome</th><th>Issues</th><th>Audit reference</th>
      </tr></thead><tbody>{reconciliation.items.map(q => <tr key={q.item_id}>
        <td>{q.signal_id}</td><td>{q.queue_state}</td><td>{q.reconciled_outcome}</td>
        <td>{q.issues.length ? q.issues.join(', ') : 'None recorded'}</td>
        <td title={q.journal_reference || ''} className="oe-hash">{shortHash(q.journal_reference)}</td>
      </tr>)}</tbody></table></div>}
    </Panel>}
    <Panel title="Operator alerts & evidence warnings" testid="offline-alerts">
      {alerts.length === 0 ? <p className="oe-muted">No informational alerts in this read. This does not certify live safety.</p> :
      <ul className="oe-alerts">{alerts.map(a => <li key={a.id}>
        <StatusChip text={a.severity} tone={a.severity === 'critical' ? 'critical' : a.severity === 'warning' ? 'warning' : 'neutral'} />
        <span>{a.text}</span>
      </li>)}</ul>}
      <p className="oe-muted">Warnings are informational and not delivered externally. They never change strategy state or broker authority.</p>
    </Panel>
  </>;
}

export default OfflineEvidencePanel;

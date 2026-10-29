/** Phase 8 focused operator UI tests — read-only, authenticated data contracts. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OfflineEvidencePanel } from '../OfflineEvidencePanel';
import type { Catalogue, OfflineStatus, OfflineSummary, DecisionEnvelope, Reconciliation, Breakdown } from '../contracts';

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();
const del = vi.fn();
vi.mock('../../../../utils/api', () => ({ api: {
  get: (...args: unknown[]) => get(...args),
  post: (...args: unknown[]) => post(...args),
  put: (...args: unknown[]) => put(...args),
  delete: (...args: unknown[]) => del(...args),
} }));

const ID = 'synthetic-strategy-id';
const catalogue: Catalogue = { read_only: true, execution_authorized: false, source: 'offline_versioned_registry',
  strategies: [{ id: ID, name: 'Synthetic Snapback', state: 'validated', locked: false, owner_scoped: true }] };
const status: OfflineStatus = { read_only: true, execution_authorized: false,
  registry: { id: ID, name: 'Synthetic Snapback', state: 'validated', locked: false },
  offline_gate: { system_state: 'approved', system_approval_mode: 'auto',
    strategy_state: 'approved', strategy_approval_mode: 'manual',
    live_execution_enabled: false, scope: 'offline_validation_only' },
  strategy_health: { score: null, status: 'not_independently_attested', reason: 'not proven' },
  versions: [{ id: 'version-1', label: 'v1', sha256: 'a'.repeat(64) }] };
const summary: OfflineSummary = {
  execution_authorized: false, metric_status: 'hypothetical_not_verified_live_profitability',
  trade_count: 2, net_pnl_inr: '390', win_rate_pct: '50', profit_factor: '2.5',
  expectancy_inr: '195', max_drawdown_inr: '200', max_drawdown_pct: '2',
  sharpe_active_utc_days: null, sortino_active_utc_days: null, average_r_multiple: '1.2',
  observed_live_exposure_inr: null,
  equity_curve: [{ at_utc: '2026-10-28T10:00:00+00:00', trade_id: 'trade1', equity_inr: '10100',
    drawdown_inr: '0', rolling_profit_factor: null, rolling_expectancy_inr: null },
    { at_utc: '2026-10-29T10:00:00+00:00', trade_id: 'trade2', equity_inr: '10390',
    drawdown_inr: '0', rolling_profit_factor: '2.5', rolling_expectancy_inr: '195' }],
  kpi_alerts: [{ code: 'INSUFFICIENT_SYNTHETIC_SAMPLE', severity: 'info', delivery: 'not_sent',
    execution_authorized: false }],
};
const decisions: DecisionEnvelope = { read_only: true, execution_authorized: false,
  items: [{ journal_event_id: 'event123', audit_ref: 'c'.repeat(64), item_id: 'item1',
    signal_id: 'BATCH1', decision_type: 'risk_decision', at_ms: 1793250000000,
    risk_state: 'offline_unverified', approval_mode: 'manual', approval_status: 'allow',
    queue_state: 'approved', run_state: 'completed', reason: 'Synthetic offline decision',
    data_quality: { status: 'good', score: 98, verified_live: false },
    risk_event_id: 'risk1', approval_event_id: 'approval1',
    lineage_sha256: 'd'.repeat(64), execution_authorized: false }] };
const reconciliation: Reconciliation = { status: 'verified_offline', execution_authorized: false, issues: [],
  items: [{ item_id: 'item1', signal_id: 'BATCH1', queue_state: 'approved',
    run_state: 'completed', reconciled_outcome: 'completed', issues: [],
    journal_reference: 'c'.repeat(64), terminal: true,
    risk_event_id: 'risk1', approval_event_id: 'approval1',
    execution_authorized: false }] };
const versions: Breakdown = { metric_status: 'hypothetical_not_verified_live_profitability',
  execution_authorized: false, groups: { 'version-1': summary } };
const symbols: Breakdown = { metric_status: 'hypothetical_not_verified_live_profitability',
  execution_authorized: false, groups: { 'NSE:INFY': summary } };

function answers(overrides: Record<string, unknown> = {}) {
  get.mockImplementation((url: string) => {
    if (url in overrides) {
      const result = overrides[url];
      if (result instanceof Error) return Promise.reject(result);
      return Promise.resolve(result);
    }
    if (url.endsWith('/offline-validation/strategies')) return Promise.resolve(catalogue);
    if (url.endsWith('/status')) return Promise.resolve(status);
    if (url.endsWith('/summary')) return Promise.resolve(summary);
    if (url.includes('/decisions')) return Promise.resolve(decisions);
    if (url.includes('/reconciliation')) return Promise.resolve(reconciliation);
    if (url.includes('group_by=version')) return Promise.resolve(versions);
    if (url.includes('group_by=symbol')) return Promise.resolve(symbols);
    return Promise.reject(new Error('unexpected GET ' + url));
  });
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const rendered = render(<QueryClientProvider client={client}><OfflineEvidencePanel /></QueryClientProvider>);
  return { ...rendered, client };
}

beforeEach(() => {
  get.mockReset(); post.mockReset(); put.mockReset(); del.mockReset();
});
afterEach(() => { vi.clearAllMocks(); });

describe('Phase 8 offline-only operator evidence dashboard', () => {
  it('renders strategy overview, synthetic KPI cards, curve, risk, versions and symbols', async () => {
    answers(); show();
    const kpis = await screen.findByTestId('offline-kpis');
    expect(within(kpis).getByText('Hypothetical net P&L')).toBeInTheDocument();
    expect(within(kpis).getByText('₹390')).toBeInTheDocument();
    expect(within(kpis).getByText('50%')).toBeInTheDocument();
    expect(within(kpis).getAllByText('2.5').length).toBeGreaterThan(0);
    expect(screen.getByTestId('offline-risk')).toHaveTextContent('manual');
    expect(screen.getByTestId('offline-risk')).toHaveTextContent('Not independently attested');
    expect(screen.getByTestId('offline-equity')).toHaveTextContent('Hypothetical equity curve');
    expect(screen.getByLabelText('Hypothetical equity curve, not live account equity')).toBeInTheDocument();
    expect(screen.getByText('version-1')).toBeInTheDocument();
    expect(screen.getByText('NSE:INFY')).toBeInTheDocument();
    expect(screen.getByText(/Not proof of profitability/)).toBeInTheDocument();
    expect(screen.getByText(/LIVE EXECUTION NOT AVAILABLE HERE/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled(); expect(put).not.toHaveBeenCalled(); expect(del).not.toHaveBeenCalled();
    expect(get.mock.calls.every(([path]) => String(path).startsWith('/api/v1/offline-validation/'))).toBe(true);
  });

  it('shows verified decision, reason, approval, quality, expandable SHA and no write actions', async () => {
    answers(); show();
    await screen.findByTestId('offline-kpis');
    fireEvent.click(screen.getByRole('tab', { name: 'Decision journal' }));
    const panel = screen.getByTestId('offline-decisions');
    expect(within(panel).getByText('Synthetic offline decision')).toBeInTheDocument();
    expect(panel).toHaveTextContent('Data: good');
    fireEvent.click(within(panel).getByText('Audit and provenance'));
    expect(panel).toHaveTextContent('c'.repeat(64));
    expect(panel).toHaveTextContent('approval1');
    expect(screen.queryByRole('button', { name: /place order|buy|sell|execute/i })).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it('shows queue state, reconciliation outcome and priority-ordered non-executing alerts', async () => {
    answers(); show();
    await screen.findByTestId('offline-kpis');
    fireEvent.click(screen.getByRole('tab', { name: 'Queue & scheduling' }));
    expect(screen.getByTestId('offline-queue')).toHaveTextContent('BATCH1');
    expect(screen.getByTestId('offline-queue')).toHaveTextContent('completed');
    expect(screen.getByTestId('offline-alerts')).toHaveTextContent('INSUFFICIENT SYNTHETIC SAMPLE');
    expect(screen.getByTestId('offline-alerts')).toHaveTextContent('health score is not independently attested');
    expect(screen.queryByRole('button', { name: /requeue|pause|start|stop/i })).toBeNull();
  });

  it('does not display invented statistics in an empty offline journal', async () => {
    answers({
      [`/api/v1/offline-validation/analytics/strategies/${ID}/summary`]: {
        ...summary, trade_count: 0, net_pnl_inr: '0', profit_factor: null, win_rate_pct: null,
        expectancy_inr: null, equity_curve: [], sharpe_active_utc_days: null,
      },
    }); show();
    await screen.findByTestId('offline-kpis');
    expect(screen.getByTestId('offline-kpis')).toHaveTextContent('Unavailable');
    expect(screen.getByTestId('offline-equity')).toHaveTextContent('No validated offline hypothetical trade curve');
  });

  it('withholds all metric cards if any source claims execution authorization', async () => {
    answers({ [`/api/v1/offline-validation/analytics/strategies/${ID}/summary`]:
      { ...summary, execution_authorized: true } });
    show(); await screen.findByTestId('offline-fail-closed');
    expect(screen.queryByTestId('offline-kpis')).toBeNull();
    expect(screen.getByTestId('offline-fail-closed')).toHaveTextContent('EVIDENCE CONTRACT INVALID');
  });

  it('withholds all metrics when source journal is untrusted or any request fails', async () => {
    answers({ [`/api/v1/offline-validation/strategies/${ID}/reconciliation?limit=100`]:
      { ...reconciliation, status: 'untrusted', issues: ['AUDIT_OR_LINEAGE_INVALID'] } });
    const r = show();
    await screen.findByTestId('offline-fail-closed');
    expect(screen.getByTestId('offline-fail-closed')).toHaveTextContent('AUDIT UNTRUSTED');
    r.unmount();
    answers({ [`/api/v1/offline-validation/analytics/strategies/${ID}/summary`]: new Error('backend unavailable') });
    show(); await screen.findByTestId('offline-fail-closed');
    expect(screen.queryByTestId('offline-kpis')).toBeNull();
  });

  it('rejects cross-strategy status responses and never shows broker status as valid', async () => {
    answers({ [`/api/v1/offline-validation/strategies/${ID}/status`]:
      { ...status, registry: { ...status.registry, id: 'wrong-strategy' } } });
    show(); await screen.findByTestId('offline-fail-closed');
    expect(screen.queryByTestId('offline-kpis')).toBeNull();
  });

  it('renders no-strategy, loading and manual refresh states without writes', async () => {
    answers({ '/api/v1/offline-validation/strategies':
      { ...catalogue, strategies: [] } });
    show();
    expect(await screen.findByText(/No versioned offline strategies/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh evidence' }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(post).not.toHaveBeenCalled();
  });

  it('displays degraded reconciliation and stale queue warnings without trading controls', async () => {
    answers({ [`/api/v1/offline-validation/strategies/${ID}/reconciliation?limit=100`]:
      { ...reconciliation, status: 'degraded', issues: ['STALE_NON_TERMINAL'],
        items: [{ ...reconciliation.items[0], issues: ['STALE_NON_TERMINAL'], terminal: false,
          queue_state: 'queued', reconciled_outcome: 'queued' }] } });
    show();
    await screen.findByTestId('offline-kpis');
    expect(screen.getByTestId('offline-alerts')).toHaveTextContent('STALE_NON_TERMINAL');
    fireEvent.click(screen.getByRole('tab', { name: 'Queue & scheduling' }));
    expect(screen.getByTestId('offline-queue')).toHaveTextContent('degraded');
    expect(post).not.toHaveBeenCalled();
  });

  it('removes previously shown numbers when the verified read becomes stale', async () => {
    answers();
    const view = show();
    await screen.findByTestId('offline-kpis');
    const originalNow = Date.now;
    const frozen = originalNow();
    const nowSpy = vi.spyOn(Date, 'now').mockImplementation(() => frozen + 5 * 60_000);
    try {
      fireEvent.click(screen.getByRole('tab', { name: 'Decision journal' }));
      await waitFor(() => expect(screen.queryByTestId('offline-kpis')).toBeNull());
      expect(screen.getByTestId('offline-fail-closed')).toHaveTextContent('EVIDENCE STALE');
    } finally {
      nowSpy.mockRestore();
      view.unmount();
    }
  });

  it('fails closed on unavailable strategy catalogue', async () => {
    answers({ '/api/v1/offline-validation/strategies': new Error('session denied') });
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent('Strategy catalogue unavailable');
    expect(screen.queryByTestId('offline-kpis')).toBeNull();
  });
});

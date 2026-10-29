/** Phase 8: contract boundary for OFFLINE operator evidence only.
 * Never infer execution permission from a validated decision, a gate state,
 * hypothetical P&L, or a cached response.
 */
export const OBSERVABILITY_ROOT = '/api/v1/offline-validation';

export type OfflineStrategy = {
  id: string; name: string; state: string; locked: boolean; owner_scoped: boolean;
};
export type Catalogue = {
  read_only: true; execution_authorized: false; source: 'offline_versioned_registry';
  strategies: OfflineStrategy[];
};
export type OfflineStatus = {
  read_only: true; execution_authorized: false;
  registry: { id: string; name: string; state: string; locked: boolean };
  offline_gate: { system_state: string; system_approval_mode: string;
    strategy_state: string; strategy_approval_mode: string;
    live_execution_enabled: false; scope: 'offline_validation_only' };
  strategy_health: { score: null; status: string; reason: string };
  versions: { id: string; label: string; sha256: string }[];
};
export type OfflineSummary = {
  execution_authorized: false;
  metric_status: 'hypothetical_not_verified_live_profitability';
  trade_count: number; net_pnl_inr: string;
  win_rate_pct: string | null; profit_factor: string | null;
  expectancy_inr: string | null; max_drawdown_inr: string | null;
  max_drawdown_pct: string | null; sharpe_active_utc_days: string | null;
  sortino_active_utc_days: string | null; average_r_multiple: string | null;
  observed_live_exposure_inr: null;
  equity_curve: { at_utc: string; trade_id: string; equity_inr: string;
    drawdown_inr: string; rolling_profit_factor: string | null;
    rolling_expectancy_inr: string | null }[];
  kpi_alerts: { code: string; severity: string; delivery: 'not_sent';
    execution_authorized: false }[];
};
export type OfflineDecision = {
  journal_event_id: string; audit_ref: string; item_id: string | null;
  signal_id: string; decision_type: string; at_ms: number;
  risk_state: string; approval_mode: string; approval_status: string;
  queue_state: string; run_state: string; reason: string;
  data_quality: { status: string; score: number | null; verified_live: false };
  risk_event_id: string | null; approval_event_id: string | null;
  lineage_sha256: string; execution_authorized: false;
};
export type DecisionEnvelope = { read_only: true; execution_authorized: false;
  items: OfflineDecision[] };
export type Reconciliation = {
  status: 'verified_offline' | 'degraded' | 'untrusted';
  execution_authorized: false; issues: string[];
  items: { item_id: string; signal_id: string; queue_state: string;
    run_state: string; reconciled_outcome: string; issues: string[];
    journal_reference: string | null; terminal: boolean;
    risk_event_id: string | null; approval_event_id: string | null;
    execution_authorized: false }[];
};
export type Breakdown = {
  metric_status: 'hypothetical_not_verified_live_profitability';
  execution_authorized: false; groups: Record<string, OfflineSummary>;
};

/** Reject malformed, authoritative, or mixed-provenance network responses. */
export function evidenceIsReadOnly(catalogue: Catalogue | undefined,
  status: OfflineStatus | undefined, summary: OfflineSummary | undefined,
  decisions: DecisionEnvelope | undefined, reconciliation: Reconciliation | undefined,
  versions: Breakdown | undefined, symbols: Breakdown | undefined): boolean {
  if (!catalogue || !status || !summary || !decisions || !reconciliation || !versions || !symbols) return false;
  if (catalogue.read_only !== true || catalogue.execution_authorized !== false ||
      catalogue.source !== 'offline_versioned_registry' ||
      status.read_only !== true || status.execution_authorized !== false ||
      status.offline_gate?.live_execution_enabled !== false ||
      status.offline_gate?.scope !== 'offline_validation_only' ||
      summary.execution_authorized !== false ||
      summary.metric_status !== 'hypothetical_not_verified_live_profitability' ||
      summary.observed_live_exposure_inr !== null ||
      decisions.read_only !== true || decisions.execution_authorized !== false ||
      reconciliation.execution_authorized !== false ||
      !['verified_offline','degraded','untrusted'].includes(reconciliation.status) ||
      versions.execution_authorized !== false || symbols.execution_authorized !== false ||
      versions.metric_status !== 'hypothetical_not_verified_live_profitability' ||
      symbols.metric_status !== 'hypothetical_not_verified_live_profitability') return false;
  if (!Array.isArray(catalogue.strategies) || !Array.isArray(decisions.items) ||
      !Array.isArray(reconciliation.items) || !Array.isArray(summary.kpi_alerts) ||
      !Array.isArray(summary.equity_curve)) return false;
  if (decisions.items.some(row => row.execution_authorized !== false ||
      row.data_quality?.verified_live !== false) ||
      reconciliation.items.some(row => row.execution_authorized !== false) ||
      summary.kpi_alerts.some(row => row.execution_authorized !== false ||
        row.delivery !== 'not_sent') ||
      Object.values(versions.groups ?? {}).some(row => row.execution_authorized !== false) ||
      Object.values(symbols.groups ?? {}).some(row => row.execution_authorized !== false)) return false;
  return true;
}

/**
 * The Operations screen's data contract (GET /api/v1/operations/family).
 *
 * Every value here is decided by the backend. The browser renders it and computes
 * nothing: no health, no eligibility, no risk. A screen that could reason about
 * whether trading is allowed is a screen that can be wrong about it.
 *
 * `null` always means UNKNOWN. It never means false, zero, or "none".
 */

/** PASS / FAIL / UNKNOWN. Amber is not green; UNKNOWN blocks wherever the backend says it blocks. */
export type TriState = 'PASS' | 'FAIL' | 'UNKNOWN';

/** Sterling has one execution mode, live. These are the states the backend can report. */
export type ProductionMode =
  | 'DEVELOPMENT'
  | 'LIVE'
  | 'LIVE_HALTED'
  | 'RECOVERY_REQUIRED'
  | 'UNKNOWN';

export type SystemStatus =
  | 'HEALTHY'
  | 'DEGRADED'
  | 'HALTED'
  | 'RECOVERY_REQUIRED'
  | 'RECONCILING'
  | 'UNKNOWN';

export interface AdmissionBlocker {
  code: string;
  severity: 'BLOCK' | 'WARN';
  message: string;
  source: string;
}

export interface SafetyStatus {
  safety_state: string;
  execution_control_state: string;
  /** null is UNKNOWN and must be shown as blocked. */
  new_risk_allowed: boolean | null;
  /** The operator's stop on all new trades. */
  family_stop_engaged: boolean | null;
  /** false means new entries are switched off (kill switch). */
  live_execution_enabled: boolean;
  blockers: AdmissionBlocker[];
}

export interface ManagedPosition {
  instrument: string;
  managed_by_sterling: true;
}

/** A broker position Sterling did not open. Never merged with managed positions. */
export interface ExternalPosition {
  source: 'BROKER_EXTERNAL';
  managed_by_sterling: false;
  account: string;
  instrument: string;
  exchange?: string | null;
  product?: string | null;
  quantity: number;
  broker_avg_price?: number | null;
  last_price?: number | null;
  unrealised_pnl?: number | null;
  discovery_reason: string;
  protection_known: boolean;
  sterling_intent: null;
  sterling_fill: null;
  observed_at: string;
}

export interface BrokerStatus {
  account_id: string | null;
  binding_id: string | null;
  connected: boolean | null;
  broker_flatness: TriState;
  /** false means the broker could not be read: an empty list is NOT flat. */
  broker_state_readable: boolean | null;
  broker_state_detail: string;
  broker_observed_at: string | null;
  managed_positions: ManagedPosition[];
  external_positions: ExternalPosition[];
  unresolved_intents: number | null;
  open_exposure: TriState;
}

export interface DeploymentStatus {
  static_egress: TriState;
  expected_egress_ip: string | null;
  observed_egress_ip: string | null;
  deployment_identity: TriState;
  lake_mount: TriState;
  network_path: TriState;
  market_freshness: TriState;
  last_tick_at: string | null;
  backend_reachable: boolean;
}

export interface SecurityStatus {
  environment: 'development' | 'production' | 'unknown';
  production_security: TriState;
  stored_secret_count: number | null;
  dev_fallback_in_use: boolean | null;
  last_migration_at: string | null;
}

export interface OperatorActions {
  stop_available: boolean;
  resume_available: boolean;
  stop_engaged: boolean | null;
}

export interface LiveOperations {
  schema_version: number;
  generated_at: string;
  runtime_sha: string | null;
  release_tag: string | null;
  environment: string;
  system_status: SystemStatus;
  production_mode: ProductionMode;
  new_risk_allowed: boolean | null;
  new_risk_blockers: string[];
  next_safe_action: string;
  safety: SafetyStatus;
  broker: BrokerStatus;
  deployment: DeploymentStatus;
  security: SecurityStatus;
  operator: OperatorActions;
}

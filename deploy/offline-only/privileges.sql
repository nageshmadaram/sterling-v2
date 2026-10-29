-- Phase 10: EXECUTE ONLY on an isolated offline-validation PostgreSQL
-- database after migrations, as its separate migration owner.
-- Never run against Sterling's existing live/broker database.
-- No LOGIN passwords/role credentials belong in source.
BEGIN;
-- Fail before any shared-cluster role or grant changes on an unexpected DB.
-- Database naming is additional defense, NOT proof of physical isolation.
DO $phase10_guard$
BEGIN
  IF current_database() !~ '^sterling_(alert_ci|offline_[a-z0-9_]+)$' THEN
    RAISE EXCEPTION 'Phase 10 privilege setup refused: non-isolated database';
  END IF;
END
$phase10_guard$;

DO $phase10_role$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sterling_offline_runtime') THEN
    CREATE ROLE sterling_offline_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END
$phase10_role$;

-- PostgreSQL grants TEMPORARY on databases to PUBLIC by default.
-- A temp relation could shadow an unqualified public audit table name.
DO $phase10_database_grants$
BEGIN
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM sterling_offline_runtime',current_database());
END
$phase10_database_grants$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sterling_offline_runtime;
GRANT USAGE ON SCHEMA public TO sterling_offline_runtime;
GRANT SELECT ON
  platform_strategies,platform_strategy_versions,platform_strategy_runs,
  platform_strategy_audit,platform_gate_events,
  platform_orchestration_events,platform_decision_journal,
  platform_hypothetical_trade_events,platform_offline_alert_events,
  platform_offline_health_events
  TO sterling_offline_runtime;
-- Metadata-only acknowledgements, resolution and audited event receipts.
GRANT INSERT ON platform_offline_alert_events,platform_offline_health_events TO sterling_offline_runtime;
REVOKE UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES ON
  platform_strategies,platform_strategy_versions,platform_strategy_runs,
  platform_strategy_audit,platform_gate_events,
  platform_orchestration_events,platform_decision_journal,
  platform_hypothetical_trade_events,platform_offline_alert_events,
  platform_offline_health_events
  FROM sterling_offline_runtime;
COMMIT;
-- A separate administrator creates a unique non-owner LOGIN account
-- and grants membership in sterling_offline_runtime only.

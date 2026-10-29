-- Phase 10: EXECUTE ONLY on an isolated, disposable offline-validation
-- PostgreSQL database after migrations, as a separate migration owner.
-- Never run against Sterling's existing live/broker database.
-- No LOGIN passwords/role credentials belong in source.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sterling_offline_runtime') THEN
    CREATE ROLE sterling_offline_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;
-- PostgreSQL grants TEMPORARY on databases to PUBLIC by default.
-- A temporary relation can shadow an unqualified public audit table name,
-- so the offline runtime MUST NOT have database TEMP or CREATE privileges.
-- Execute against only the caller's explicitly selected isolated database.
DO $
BEGIN
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM sterling_offline_runtime',current_database());
END $;
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
-- Metadata-only acknowledgements, resolution, and offline event receipt
-- creation append to exactly one audited table. Runtime cannot update,
-- delete, truncate, rewrite registry, or alter triggers.
GRANT INSERT ON platform_offline_alert_events,platform_offline_health_events TO sterling_offline_runtime;
REVOKE UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES ON
  platform_strategies,platform_strategy_versions,platform_strategy_runs,
  platform_strategy_audit,platform_gate_events,
  platform_orchestration_events,platform_decision_journal,
  platform_hypothetical_trade_events,platform_offline_alert_events,
  platform_offline_health_events
  FROM sterling_offline_runtime;
COMMIT;
-- A separate administrator creates a unique non-owner LOGIN and grants
-- membership in sterling_offline_runtime; do not make it schema/table owner.

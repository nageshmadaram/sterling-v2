"""Offline-only Phase10 database privilege preflight, no migrations or writes."""
from sqlalchemy import text
from app.persistence.base import make_engine

def assert_runtime_privileges(engine=None):
    own=engine is None
    engine=engine or make_engine()
    try:
        if engine.dialect.name!="postgresql":
            raise RuntimeError("PostgreSQL required for offline security profile")
        with engine.connect() as conn:
            checks=conn.execute(text("""
              SELECT current_user AS role_name,
                (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser,
                (SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user) AS create_role,
                (SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user) AS create_db,
                has_schema_privilege(current_user,'public','CREATE') AS schema_create,
                has_table_privilege(current_user,'platform_strategies','UPDATE') AS registry_update,
                has_table_privilege(current_user,'platform_strategy_versions','INSERT') AS version_insert,
                has_table_privilege(current_user,'platform_decision_journal','INSERT') AS journal_insert,
                has_table_privilege(current_user,'platform_offline_alert_events','UPDATE') AS alert_update,
                has_table_privilege(current_user,'platform_offline_alert_events','DELETE') AS alert_delete,
                has_table_privilege(current_user,'platform_offline_health_events','UPDATE') AS health_update,
                has_table_privilege(current_user,'platform_offline_health_events','INSERT') AS health_insert,
                has_table_privilege(current_user,'platform_offline_alert_events','INSERT') AS alert_insert,
                has_table_privilege(current_user,'platform_offline_alert_events','SELECT') AS alert_read
            """)).mappings().one()
            bad=("superuser","create_role","create_db","schema_create",
                 "registry_update","version_insert","journal_insert",
                 "alert_update","alert_delete","health_update")
            if any(checks[key] for key in bad):
                raise RuntimeError("offline runtime database role has prohibited privileges")
            if not checks["alert_insert"] or not checks["alert_read"] or not checks["health_insert"]:
                raise RuntimeError("offline runtime lacks required audited alert append/read")
            owners=conn.execute(text("""
                SELECT c.relname
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname='public' AND c.relname IN
                  ('platform_strategies','platform_strategy_versions',
                   'platform_gate_events','platform_orchestration_events',
                   'platform_decision_journal','platform_hypothetical_trade_events',
                   'platform_offline_alert_events','platform_offline_health_events')
                AND pg_get_userbyid(c.relowner)=current_user
            """)).scalars().all()
            if owners: raise RuntimeError("offline runtime owns audited table")
            return {"database":"role_grants_verified","read_only_registry":True,
                    "append_only_alerts":True,"execution_authorized":False}
    finally:
        if own: engine.dispose()

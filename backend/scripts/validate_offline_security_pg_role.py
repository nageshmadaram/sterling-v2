"""Phase 10 privilege-isolation probe, disposable PG16 only, never production."""
import os
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.persistence.base import make_engine
from app.core.offline_database_privileges import assert_runtime_privileges

def main():
    url=os.environ.get("DATABASE_URL","")
    if (os.environ.get("STERLING_ALERT_DISPOSABLE_DB")!="yes"
        or not url.startswith("postgresql+")
        or not url.split("?",1)[0].endswith("/sterling_alert_ci")):
        raise RuntimeError("destructive security probe may run only on CI disposable database")
    engine=make_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET ROLE sterling_offline_runtime"))
            try:
                role=conn.execute(text("SELECT current_user")).scalar_one()
                assert role=="sterling_offline_runtime"
                assert conn.execute(text("SELECT count(*) FROM platform_offline_alert_events")).scalar_one()>=4
                for stmt in [
                    "UPDATE platform_strategies SET display_name='forge'",
                    "DELETE FROM platform_strategy_versions",
                    "TRUNCATE platform_offline_alert_events",
                    "UPDATE platform_offline_alert_events SET reason='forge'",
                    "DELETE FROM platform_offline_alert_events",
                    "INSERT INTO platform_strategy_audit (id) VALUES ('forged')",
                    "INSERT INTO platform_decision_journal (id) VALUES ('forged')",
                    "ALTER TABLE platform_offline_alert_events DISABLE TRIGGER ALL",
                    "SELECT id FROM platform_strategies FOR UPDATE",
                ]:
                    sp=conn.begin_nested()
                    try:
                        conn.execute(text(stmt))
                    except DBAPIError:
                        sp.rollback()
                    else:
                        sp.rollback()
                        raise AssertionError("offline runtime may perform prohibited database operation: "+stmt)
                permissions=conn.execute(text("""
                    SELECT
                      has_schema_privilege(current_user,'public','CREATE'),
                      has_table_privilege(current_user,'platform_offline_alert_events','UPDATE'),
                      has_table_privilege(current_user,'platform_strategy_versions','INSERT'),
                      has_table_privilege(current_user,'platform_offline_alert_events','INSERT')
                """)).one()
                assert permissions==(False,False,False,True)
                print("PASS: PostgreSQL16 role grants; no registry/ledger mutation, schema DDL, row locks or trigger disable")
            finally:
                conn.rollback()  # SET ROLE was transaction-local
    finally:
        engine.dispose()

if __name__=="__main__":
    main()

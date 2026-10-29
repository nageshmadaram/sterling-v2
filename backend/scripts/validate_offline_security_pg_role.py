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
                from pathlib import Path
                import json
                fixture=Path(__file__).resolve().parent/"fixtures"/"offline_role_denials.json"
                forbidden=json.loads(fixture.read_text(encoding="utf-8"))["statements"]
                for stmt in forbidden:
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
        # Functional smoke: the actual restricted role must still support
        # signed owner-scoped offline reads and explicit health append.
        from sqlalchemy import event
        from types import SimpleNamespace
        from app.persistence.session import make_session_factory
        from app.persistence import strategy_registry_service as registry
        from app.persistence.offline_alert_service import OfflineAlertService
        from app.persistence.offline_performance_service import OfflinePerformanceService
        from app.persistence.offline_health_service import OfflineHealthRecorder
        with engine.connect() as conn:
            strategy_id=conn.execute(text("SELECT id FROM platform_strategies LIMIT 1")).scalar_one()
        runtime=make_engine(url)
        @event.listens_for(runtime,"connect")
        def assume_restricted_role(dbapi_connection,record):
            cursor=dbapi_connection.cursor()
            cursor.execute("SET ROLE sterling_offline_runtime")
            cursor.close()
        previous=registry._identity
        registry._identity=lambda _:SimpleNamespace(user_id="synthetic_admin",role="admin",view=None,owner_uid=None)
        try:
            sessions=make_session_factory(runtime)
            assert len(OfflineAlertService(sessions).list(
                access_token="synthetic",strategy_id=strategy_id))==1
            performance=OfflinePerformanceService(sessions).report(
                access_token="synthetic",strategy_id=strategy_id)
            assert performance["execution_authorized"] is False
            health=OfflineHealthRecorder(sessions)
            health_id=health.sample_once(access_token="synthetic",strategy_id=strategy_id)
            assert health_id
            replay=health.read(access_token="synthetic",strategy_id=strategy_id)
            assert len(replay["snapshots"])==1
            assert replay["snapshots"][0]["execution_authorized"] is False
            print("PASS: restricted application role can read offline analytics/alerts and append one immutable health snapshot")
        finally:
            registry._identity=previous
            runtime.dispose()
    finally:
        engine.dispose()

if __name__=="__main__":
    main()

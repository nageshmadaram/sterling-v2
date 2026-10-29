"""Disposable PostgreSQL16 audit/restore verification for Phase 5 OFFLINE queue."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.execution_validation import RiskPolicy
from app.domain.offline_scheduler import ScheduleKind, ScheduleSpec
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.persistence.offline_scheduler_service import OfflineScheduler
from app.persistence import strategy_registry_service as registry


def main():
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("STERLING_SCHEDULE_DISPOSABLE_DB") != "yes":
        raise RuntimeError("disposable database proof required")
    if not url.startswith("postgresql+") or not url.split("?", 1)[0].endswith("/sterling_queue_ci"):
        raise RuntimeError("refusing non-CI Postgres target")
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    prior = registry._identity
    registry._identity = lambda _: SimpleNamespace(
        user_id="synthetic_admin", role="admin", view=None, owner_uid=None)
    try:
        sessions = make_session_factory(engine)
        registry_service = registry.StrategyRegistryService(sessions)
        strategy = registry_service.create_strategy(
            access_token="synthetic", slug="postgres_queue",
            display_name="Offline orchestration", reason="disposable DB")
        version = registry_service.publish_version(
            access_token="synthetic", strategy_id=strategy, version="v1",
            parameters={"window": 10}, reason="synthetic version")
        run = registry_service.create_offline_run(
            access_token="synthetic", version_id=version,
            mode="simulation", reason="synthetic run")
        scheduler = OfflineScheduler(sessions, OfflineExecutionGate(sessions))
        now = datetime.now(timezone.utc)
        schedule = ScheduleSpec(
            schedule_id=str(uuid4()), kind=ScheduleKind.MANUAL,
            strategy_id=strategy, version_id=version, run_id=run,
            batch_id="postgres-batch", qualified_symbol="NSE:INFY",
            start_at_utc=now-timedelta(seconds=1),
        )
        risk = RiskPolicy(
            max_exposure_inr=100000, max_daily_loss_inr=1000,
            max_session_loss_inr=500, max_order_notional_inr=10000,
            max_units=50,
        )
        scheduler.schedule(access_token="synthetic", spec=schedule,
                           risk_policy=risk, reason="disposable CI")
        materialized = scheduler.materialize_due(
            access_token="synthetic", strategy_id=strategy,
            clock=now, reason="CI due")
        assert len(materialized)==1
        assert materialized[0].execution_authorized is False
        assert scheduler.materialize_due(
            access_token="synthetic", strategy_id=strategy,
            clock=now, reason="deduplicate") == ()
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM platform_orchestration_events"
            )).scalar_one()==3
        for sql in (
            "UPDATE platform_orchestration_events SET reason='tampered'",
            "DELETE FROM platform_orchestration_events",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(text(sql))
            except DBAPIError:
                pass
            else:
                raise AssertionError("PostgreSQL audit trigger failed: "+sql)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE FUNCTION sterling_queue_ci_reject_insert() RETURNS TRIGGER AS $$
                BEGIN RAISE EXCEPTION 'synthetic offline queue outage';
                END; $$ LANGUAGE plpgsql
            """))
            conn.execute(text("""
                CREATE TRIGGER sterling_queue_ci_fail BEFORE INSERT ON platform_orchestration_events
                FOR EACH ROW EXECUTE FUNCTION sterling_queue_ci_reject_insert()
            """))
        try:
            try:
                scheduler.set_pause(access_token="synthetic",
                                    strategy_id=strategy,
                                    schedule_id=schedule.schedule_id,
                                    paused=True,reason="expect rollback")
            except DBAPIError:
                pass
            else:
                raise AssertionError("audit failure did not roll back schedule pause")
            with engine.connect() as conn:
                assert conn.execute(text(
                    "SELECT count(*) FROM platform_orchestration_events"
                )).scalar_one()==3
        finally:
            with engine.begin() as conn:
                conn.execute(text(
                    "DROP TRIGGER sterling_queue_ci_fail ON platform_orchestration_events"
                ))
                conn.execute(text("DROP FUNCTION sterling_queue_ci_reject_insert()"))
        print("PASS: PostgreSQL migration, immutable queue audit, bounded due, duplicate guard and rollback")
    finally:
        registry._identity = prior
        engine.dispose()


if __name__ == "__main__":
    main()

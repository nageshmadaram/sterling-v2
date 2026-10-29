"""Phase 6 PostgreSQL 16 journal atomicity/restore; disposable CI only."""
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.execution_validation import RiskPolicy
from app.domain.offline_scheduler import ScheduleSpec,ScheduleKind
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.offline_scheduler_service import OfflineScheduler
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.persistence import strategy_registry_service as registry

def main():
    url=os.environ.get("DATABASE_URL","")
    if (os.getenv("STERLING_JOURNAL_DISPOSABLE_DB")!="yes"
        or not url.startswith("postgresql+")
        or not url.split("?",1)[0].endswith("/sterling_journal_ci")):
        raise RuntimeError("refuse non-disposable Phase 6 database")
    backend=Path(__file__).resolve().parents[1]
    cfg=Config(str(backend/"alembic.ini"))
    cfg.set_main_option("script_location",str(backend/"alembic"))
    command.upgrade(cfg,"head")
    engine=make_engine(url)
    prior=registry._identity
    registry._identity=lambda _:SimpleNamespace(user_id="synthetic_admin",role="admin",
                                                 view=None,owner_uid=None)
    try:
        factory=make_session_factory(engine)
        reg=registry.StrategyRegistryService(factory)
        sid=reg.create_strategy(access_token="synthetic",slug="phase6",
                                display_name="Journal CI",reason="synthetic")
        vid=reg.publish_version(access_token="synthetic",strategy_id=sid,
                                version="1",parameters={},reason="synthetic")
        run=reg.create_offline_run(access_token="synthetic",version_id=vid,
                                   mode="simulation",reason="synthetic")
        scheduler=OfflineScheduler(factory,OfflineExecutionGate(factory))
        spec=ScheduleSpec(schedule_id=str(uuid4()),kind=ScheduleKind.MANUAL,
                          strategy_id=sid,version_id=vid,run_id=run,
                          batch_id="pg-journal",qualified_symbol="NSE:INFY",
                          start_at_utc=datetime.now(timezone.utc)-timedelta(seconds=1))
        policy=RiskPolicy(max_exposure_inr=100000,max_daily_loss_inr=2000,
                          max_session_loss_inr=1500,max_order_notional_inr=15000,
                          max_units=50)
        scheduler.schedule(access_token="synthetic",spec=spec,risk_policy=policy,
                           reason="journal migrate")
        item=scheduler.materialize_due(access_token="synthetic",strategy_id=sid,
                              clock=datetime.now(timezone.utc),reason="queue materialize")
        assert len(item)==1
        rows=OperatorDecisionJournal(factory).decisions(
            access_token="synthetic",strategy_id=sid)
        assert len(rows)==3 and all(x["execution_authorized"] is False for x in rows)
        for query in ("UPDATE platform_decision_journal SET reason='tamper'",
                      "DELETE FROM platform_decision_journal",
                      "UPDATE platform_decision_journal SET execution_authorized=true"):
            try:
                with engine.begin() as db: db.execute(text(query))
            except DBAPIError: pass
            else: raise AssertionError("PG journal immutability failed")
        with engine.begin() as db:
            db.execute(text("""
            CREATE FUNCTION sterling_journal_ci_reject() RETURNS TRIGGER AS $$
            BEGIN RAISE EXCEPTION 'synthetic journal insertion outage'; END;
            $$ LANGUAGE plpgsql
            """))
            db.execute(text("""
            CREATE TRIGGER sterling_journal_ci_fail BEFORE INSERT ON platform_decision_journal
            FOR EACH ROW EXECUTE FUNCTION sterling_journal_ci_reject()
            """))
        try:
            try:
                scheduler.set_pause(access_token="synthetic",strategy_id=sid,
                                    schedule_id=spec.schedule_id,paused=True,
                                    reason="forced journal rollback")
            except DBAPIError: pass
            else: raise AssertionError("journal insert did not abort queue mutation")
            with engine.connect() as db:
                assert db.execute(text("SELECT count(*) FROM platform_decision_journal")).scalar_one()==3
                assert db.execute(text("SELECT count(*) FROM platform_orchestration_events")).scalar_one()==3
        finally:
            with engine.begin() as db:
                db.execute(text("DROP TRIGGER sterling_journal_ci_fail ON platform_decision_journal"))
                db.execute(text("DROP FUNCTION sterling_journal_ci_reject()"))
        print("PASS: PG16 atomic journal+queue rollback, immutable triggers, owner-scoped evidence")
    finally:
        registry._identity=prior
        engine.dispose()

if __name__=="__main__":
    main()

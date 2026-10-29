"""Disposable PostgreSQL 16 Phase 7 synthetic trade audit/restore proof."""
from __future__ import annotations
import os
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.domain.execution_validation import (
    ApprovalMode, ExecutionMode, GateInput, GateState, RiskEvidence,
    RiskPolicy, SignalIntent, StrategyHealth,
)
from app.domain.offline_scheduler import ScheduleKind,ScheduleSpec
from app.domain.offline_performance import HypotheticalFillPair,Side
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence import strategy_registry_service as registry
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.persistence.offline_scheduler_service import OfflineScheduler
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.offline_performance_service import OfflinePerformanceService

def main():
    url=os.environ.get("DATABASE_URL","")
    if (os.getenv("STERLING_ANALYTICS_DISPOSABLE_DB")!="yes"
        or not url.startswith("postgresql+")
        or not url.split("?",1)[0].endswith("/sterling_analytics_ci")):
        raise RuntimeError("refuse non-disposable Phase 7 database")
    root=Path(__file__).resolve().parents[1]
    cfg=Config(str(root/"alembic.ini"))
    cfg.set_main_option("script_location",str(root/"alembic"))
    command.upgrade(cfg,"head")
    engine=make_engine(url)
    previous=registry._identity
    registry._identity=lambda _:SimpleNamespace(user_id="synthetic_admin",role="admin",view=None,owner_uid=None)
    try:
        sessions=make_session_factory(engine)
        registry_service=registry.StrategyRegistryService(sessions)
        strategy=registry_service.create_strategy(access_token="synthetic",slug="offline_kpi_pg",
                                                   display_name="Synthetic KPI",reason="PG disposable")
        version=registry_service.publish_version(access_token="synthetic",strategy_id=strategy,
                                                 version="v1",parameters={"size":5},reason="PG")
        run=registry_service.create_offline_run(access_token="synthetic",version_id=version,
                                                mode="simulation",reason="PG")
        gate=OfflineExecutionGate(sessions)
        for sid in (None,strategy):
            gate.transition(access_token="synthetic",strategy_id=sid,new_state=GateState.VALIDATION_REQUIRED,
                            approval_mode=ApprovalMode.BLOCKED,reason="offline PG validation")
            gate.transition(access_token="synthetic",strategy_id=sid,new_state=GateState.APPROVED,
                            approval_mode=ApprovalMode.AUTO,reason="offline PG approved")
        scheduler=OfflineScheduler(sessions,gate)
        risk=RiskPolicy(max_exposure_inr=100000,max_daily_loss_inr=5000,max_session_loss_inr=5000,
                        max_order_notional_inr=15000,max_units=100)
        def complete(batch):
            stamp=datetime.now(timezone.utc)
            spec=ScheduleSpec(schedule_id=str(uuid4()),kind=ScheduleKind.MANUAL,
                              strategy_id=strategy,version_id=version,run_id=run,
                              batch_id=batch,qualified_symbol="NSE:INFY",
                              start_at_utc=stamp-timedelta(seconds=1))
            scheduler.schedule(access_token="synthetic",spec=spec,risk_policy=risk,reason="synthetic schedule")
            item=scheduler.materialize_due(access_token="synthetic",strategy_id=strategy,
                                           clock=stamp,reason="synthetic due")[0]
            evidence=GateInput(
                signal=SignalIntent(signal_id=batch,order_intent_id=item.identity.item_id,
                    strategy_id=strategy,version_id=version,run_id=run,
                    qualified_symbol="NSE:INFY",mode=ExecutionMode.SIMULATION,
                    units=Decimal(10),estimated_notional_inr=Decimal(1000)),
                policy=risk,
                health=StrategyHealth(valid_parameters=True,has_risk_profile=True,
                    valid_market_access=True,recent_performance=Decimal("0.02"),
                    configuration_at_utc=stamp,operational=True),
                exposure=RiskEvidence(current_exposure_inr=0,daily_loss_inr=0,session_loss_inr=0,
                    provider_healthy=True,market_open=True,risk_profile_verified=True,
                    symbol_valid=True,source_verified=True),
                market_quality_status="good",market_quality_score=98,
                market_regime="trending",at_utc=stamp,
            )
            result=scheduler.evaluate(access_token="synthetic",strategy_id=strategy,
                                      item_id=item.identity.item_id,inputs=evidence,
                                      reason="synthetic PG risk")
            assert result.backlog.value=="approved"
            scheduler.complete_preview(access_token="synthetic",strategy_id=strategy,
                                       item_id=item.identity.item_id,reason="PG completed")
            events=OperatorDecisionJournal(sessions).decisions(
                access_token="synthetic",strategy_id=strategy,limit=None)
            matched=[e for e in events if e["item_id"]==item.identity.item_id
                     and e["run_state"]=="completed" and e["decision_type"]=="run_transition"]
            assert len(matched)==1
            return matched[0]["journal_event_id"]
        entry=complete("PG-ENTRY")
        exit=complete("PG-EXIT")
        now=datetime.now(timezone.utc)
        fixture=HypotheticalFillPair(entry_decision_id=entry,exit_decision_id=exit,
                 entry_at_utc=now-timedelta(hours=3),exit_at_utc=now-timedelta(hours=1),
                 entry_price=100,exit_price=112,units=10,side=Side.LONG,
                 fees_inr=5,slippage_inr=2,market_regime="trending",
                 entry_reason="synthetic input",exit_reason="synthetic input",
                 operator_reason="synthetic PG analytics validation")
        analytics=OfflinePerformanceService(sessions)
        trade_id=analytics.record_hypothetical(access_token="synthetic",
                                                strategy_id=strategy,fixture=fixture)
        assert trade_id
        summary=analytics.report(access_token="synthetic",strategy_id=strategy,
                                 starting_equity_inr=Decimal(10000))
        assert summary["net_pnl_inr"]=="113" and summary["execution_authorized"] is False
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM platform_hypothetical_trade_events")).scalar_one()==1
        for sql in (
            "UPDATE platform_hypothetical_trade_events SET reason='tamper'",
            "DELETE FROM platform_hypothetical_trade_events",
            "UPDATE platform_hypothetical_trade_events SET execution_authorized=true",
        ):
            try:
                with engine.begin() as conn: conn.execute(text(sql))
            except DBAPIError: pass
            else: raise AssertionError("PostgreSQL analytics immutable trigger missing")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE FUNCTION sterling_analytics_ci_reject() RETURNS TRIGGER AS $$
                BEGIN RAISE EXCEPTION 'synthetic analytics outage'; END;
                $$ LANGUAGE plpgsql
            """))
            conn.execute(text("""
                CREATE TRIGGER sterling_analytics_ci_fail BEFORE INSERT
                ON platform_hypothetical_trade_events FOR EACH ROW
                EXECUTE FUNCTION sterling_analytics_ci_reject()
            """))
        try:
            new_exit=complete("PG-SECOND-EXIT")
            fixture2=fixture.model_copy(update={"exit_decision_id":new_exit})
            try:
                analytics.record_hypothetical(access_token="synthetic",
                                               strategy_id=strategy,fixture=fixture2)
            except DBAPIError: pass
            else: raise AssertionError("synthetic ledger outage did not roll back")
            with engine.connect() as conn:
                assert conn.execute(text("SELECT COUNT(*) FROM platform_hypothetical_trade_events")).scalar_one()==1
        finally:
            with engine.begin() as conn:
                conn.execute(text("DROP TRIGGER sterling_analytics_ci_fail ON platform_hypothetical_trade_events"))
                conn.execute(text("DROP FUNCTION sterling_analytics_ci_reject()"))
        print("PASS: PostgreSQL16 hypothetical ledger, signed decision lineage, immutable triggers, rollback and synthetic arithmetic")
    finally:
        registry._identity=previous
        engine.dispose()

if __name__=="__main__":
    main()

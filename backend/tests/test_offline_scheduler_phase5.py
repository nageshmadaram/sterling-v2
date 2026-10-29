"""Phase 5 end-to-end offline scheduling; disposable DB and synthetic actors only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.execution_validation import (
    ApprovalMode, ExecutionMode, GateInput, GateState, RiskEvidence,
    RiskPolicy, SignalIntent, StrategyHealth,
)
from app.domain.offline_scheduler import (
    BacklogState, RunState, ScheduleKind, ScheduleSpec, require_pair,
)
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.persistence.offline_scheduler_service import OfflineScheduler
from app.persistence import strategy_registry_service as registry

UTC = timezone.utc


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from app.core.config import settings
    url = f"sqlite:///{tmp_path / 'offline_phase5.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")
    engine = make_engine(url)

    def actor(token):
        if token == "admin":
            return SimpleNamespace(user_id="owner", role="admin", view=None, owner_uid=None)
        if token == "operator":
            return SimpleNamespace(user_id="owner", role="operator", view=None, owner_uid=None)
        if token == "outside":
            return SimpleNamespace(user_id="another_owner", role="operator",
                                   view=None, owner_uid=None)
        raise PermissionError("signed identity rejected")
    monkeypatch.setattr(registry, "_identity", actor)
    factory = make_session_factory(engine)
    reg = registry.StrategyRegistryService(factory)
    strategy = reg.create_strategy(access_token="admin", slug="phase5",
                                    display_name="Offline schedule", reason="fixture")
    version = reg.publish_version(access_token="admin", strategy_id=strategy,
                                  version="v1", parameters={"period": 5},
                                  reason="synthetic immutable version")
    run = reg.create_offline_run(access_token="admin", version_id=version,
                                 mode="simulation", reason="synthetic offline run")
    gate = OfflineExecutionGate(factory)
    service = OfflineScheduler(factory, gate)
    try:
        yield SimpleNamespace(engine=engine, reg=reg, gate=gate, service=service,
                              strategy=strategy, version=version, run=run)
    finally:
        engine.dispose()


def policy():
    return RiskPolicy(max_exposure_inr=100000, max_daily_loss_inr=2000,
                      max_session_loss_inr=1500, max_order_notional_inr=20000,
                      max_units=100)


def schedule(lab, *, kind=ScheduleKind.MANUAL, at=None, end=None,
             every=None, count=1, priority=5, age=300, batch="BATCH-1"):
    return ScheduleSpec(
        schedule_id=str(uuid4()), strategy_id=lab.strategy,
        version_id=lab.version, run_id=lab.run,
        kind=kind, batch_id=batch, qualified_symbol="NSE:INFY",
        start_at_utc=at or datetime.now(UTC)-timedelta(seconds=1),
        end_at_utc=end, cadence_seconds=every, max_occurrences=count,
        priority=priority, max_queue_age_seconds=age,
    )


def approved(lab):
    for sid in (None, lab.strategy):
        lab.gate.transition(access_token="admin", strategy_id=sid,
                            new_state=GateState.VALIDATION_REQUIRED,
                            approval_mode=ApprovalMode.BLOCKED, reason="offline proof")
        lab.gate.transition(access_token="admin", strategy_id=sid,
                            new_state=GateState.APPROVED,
                            approval_mode=ApprovalMode.AUTO, reason="offline proof verified")


def enter(lab, spec):
    lab.service.schedule(access_token="admin", spec=spec, risk_policy=policy(),
                         reason="schedule validation")
    items=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                      clock=datetime.now(UTC),reason="materialize validation")
    return items[0]


def input_for(item, *, degraded=False, signal_mode=ExecutionMode.SIMULATION):
    now = datetime.now(UTC)
    return GateInput(
        signal=SignalIntent(
            signal_id=item.identity.batch_id, order_intent_id=item.identity.item_id,
            strategy_id=item.identity.strategy_id, version_id=item.identity.version_id,
            run_id=item.identity.run_id,
            qualified_symbol=item.identity.qualified_symbol,mode=signal_mode,
            units=Decimal(5), estimated_notional_inr=Decimal(5000),
        ),
        policy=policy(),
        health=StrategyHealth(valid_parameters=True,has_risk_profile=True,
                              valid_market_access=True,operational=True,
                              configuration_at_utc=now,
                              recent_performance=Decimal("0.02")),
        exposure=RiskEvidence(
            current_exposure_inr=10000,daily_loss_inr=100,
            session_loss_inr=100,provider_healthy=True,market_open=True,
            risk_profile_verified=True,symbol_valid=True,source_verified=True,
        ),
        market_quality_status="degraded" if degraded else "good",
        market_quality_score=30 if degraded else 98,
        market_regime="trending",at_utc=now,
    )


def count(lab, kind=None):
    with lab.engine.connect() as db:
        return db.execute(text(
            "SELECT count(*) FROM platform_orchestration_events" +
            (" WHERE kind=:kind" if kind else "")
        ), {"kind":kind} if kind else {}).scalar_one()


def test_manual_deterministic_deduplication_and_lineage(lab):
    spec=schedule(lab)
    item=enter(lab,spec)
    assert item.backlog is BacklogState.QUEUED
    assert item.run_state is RunState.QUEUED
    assert item.identity.execution_authorized is False
    assert item.identity.version_sha256 and item.identity.risk_policy_sha256
    assert item.identity.lineage_sha256
    assert item.identity.batch_id=="BATCH-1"
    second=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
        clock=datetime.now(UTC),reason="idempotent replay")
    assert second==()
    assert lab.service.queue(access_token="admin",strategy_id=lab.strategy)==(item,)
    assert count(lab,"schedule_created")==1
    assert count(lab,"queue_transition")==2
    with pytest.raises(PermissionError):
        lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),
                             reason="duplicate schedule")


@pytest.mark.parametrize("kind", [ScheduleKind.MANUAL,ScheduleKind.DELAYED,ScheduleKind.SESSION])
def test_not_yet_due_stays_empty_then_enqueues(kind,lab):
    now=datetime.now(UTC)
    future=now+timedelta(minutes=10)
    spec=schedule(lab,kind=kind,at=future,
                  end=future+timedelta(minutes=15) if kind is ScheduleKind.SESSION else None)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="future")
    assert lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                       clock=now,reason="not due")==()
    queued=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                        clock=future+timedelta(seconds=1),reason="due")
    assert len(queued)==1 and queued[0].backlog is BacklogState.QUEUED


def test_recurring_ordering_and_stale_occurrences(lab):
    now=datetime.now(UTC)
    first=schedule(lab,kind=ScheduleKind.RECURRING,
                   at=now-timedelta(minutes=5),every=60,count=4,age=90)
    second=schedule(lab,kind=ScheduleKind.MANUAL,at=now-timedelta(minutes=1),
                    priority=0,batch="BATCH-2")
    for spec in (first,second):
        lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="register")
    rows=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                    clock=now,reason="stable ordered poll",limit=3,backlog_warn_at=1)
    assert len(rows)==3
    assert [item.identity.due_at_utc for item in rows]==sorted(x.identity.due_at_utc for x in rows)
    assert all(x.backlog is BacklogState.SKIPPED for x in rows)
    assert any(a.kind=="queue_growth" for a in lab.service.alerts(access_token="admin",
                                                                  strategy_id=lab.strategy)) is False
    remaining=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                           clock=now,reason="second page",limit=4)
    assert len(remaining)==2
    assert len({item.identity.item_id for item in rows+remaining})==5
    assert lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                        clock=now,reason="idempotent")==()


def test_pause_resume_and_emergency_schedule_lock(lab):
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="fixture")
    lab.service.set_pause(access_token="operator",strategy_id=lab.strategy,
                          schedule_id=spec.schedule_id,paused=True,reason="manual pause")
    assert lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                        clock=datetime.now(UTC),reason="paused")==()
    lab.service.set_pause(access_token="admin",strategy_id=lab.strategy,
                          schedule_id=spec.schedule_id,paused=False,reason="manual resume")
    assert len(lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                           clock=datetime.now(UTC),reason="resumed"))==1
    lab.gate.transition(access_token="admin",strategy_id=None,
                        new_state=GateState.EMERGENCY_STOP,
                        approval_mode=ApprovalMode.BLOCKED,reason="offline system stop")
    lab.service.set_pause(access_token="admin",strategy_id=lab.strategy,
                          schedule_id=spec.schedule_id,paused=True,reason="stop")
    with pytest.raises(PermissionError):
        lab.service.set_pause(access_token="admin",strategy_id=lab.strategy,
                              schedule_id=spec.schedule_id,paused=False,reason="resume")


def test_risk_gate_record_required_before_preview_approval(lab):
    approved(lab)
    item=enter(lab,schedule(lab))
    result=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                item_id=item.identity.item_id,
                                inputs=input_for(item),reason="evaluate offline only")
    assert result.backlog is BacklogState.APPROVED
    assert result.run_state is RunState.APPROVED
    assert result.risk_event_id and result.approval_event_id
    assert result.execution_authorized is False
    assert count(lab,"gate_binding")==1
    completed=lab.service.complete_preview(access_token="admin",strategy_id=lab.strategy,
                                          item_id=item.identity.item_id,reason="complete offline")
    assert completed.run_state is RunState.COMPLETED
    with pytest.raises(PermissionError):
        lab.service.complete_preview(access_token="admin",strategy_id=lab.strategy,
                                     item_id=item.identity.item_id,reason="immutable complete")
    with pytest.raises(PermissionError):
        lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                             item_id=item.identity.item_id,
                             inputs=input_for(item),reason="completed cannot repeat")


def test_risk_gate_rejects_stale_degraded_and_emergency_stopped(lab):
    approved(lab)
    item=enter(lab,schedule(lab))
    outcome=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                  item_id=item.identity.item_id,
                                  inputs=input_for(item,degraded=True),reason="degraded feed")
    assert outcome.backlog is BacklogState.EMERGENCY_STOPPED
    assert not outcome.execution_authorized
    assert any(alert.kind=="emergency_stop" for alert in
               lab.service.alerts(access_token="admin",strategy_id=lab.strategy))
    with pytest.raises(PermissionError):
        lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                            item_id=item.identity.item_id,inputs=input_for(item),
                            reason="no emergency requeue")


def test_requeue_requires_fresh_gate_allow_and_no_disabled_strategy(lab):
    approved(lab)
    item=enter(lab,schedule(lab))
    # Pause by evaluating while the strategy validation state is paused.
    lab.gate.transition(access_token="admin",strategy_id=lab.strategy,
                        new_state=GateState.PAUSED,approval_mode=ApprovalMode.BLOCKED,
                        reason="offline operator pause")
    paused=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                 item_id=item.identity.item_id,inputs=input_for(item),
                                 reason="paused gate")
    assert paused.backlog is BacklogState.PAUSED
    assert not lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                                  item_id=item.identity.item_id,inputs=input_for(item),
                                  reason="blocked requeue")
    lab.gate.transition(access_token="admin",strategy_id=lab.strategy,
                        new_state=GateState.VALIDATION_REQUIRED,
                        approval_mode=ApprovalMode.BLOCKED,reason="revalidate")
    lab.gate.transition(access_token="admin",strategy_id=lab.strategy,
                        new_state=GateState.APPROVED,
                        approval_mode=ApprovalMode.AUTO,reason="validation complete")
    assert lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                               item_id=item.identity.item_id,inputs=input_for(item),
                               reason="safe offline requeue")
    again=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                item_id=item.identity.item_id,inputs=input_for(item),
                                reason="new audit decision")
    assert again.backlog is BacklogState.APPROVED
    with lab.engine.begin() as db:
        db.execute(text("UPDATE platform_strategies SET state='disabled' WHERE id=:id"),
                   {"id":lab.strategy})
    with pytest.raises(PermissionError):
        lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                            item_id=item.identity.item_id,inputs=input_for(item),
                            reason="disabled cannot requeue")


def test_disabled_skips_materialization_and_no_live_intents(lab):
    item_spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=item_spec,risk_policy=policy(),
                         reason="pre-disable schedule")
    with lab.engine.begin() as db:
        db.execute(text("UPDATE platform_strategies SET state='disabled' WHERE id=:id"),
                   {"id":lab.strategy})
    items=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                      clock=datetime.now(UTC),reason="disabled")
    assert items[0].backlog is BacklogState.SKIPPED
    with pytest.raises(PermissionError):
        lab.service.schedule(access_token="admin",
                             spec=schedule(lab),risk_policy=policy(),
                             reason="disabled cannot schedule")


def test_contract_enforces_no_bypass_of_evaluating_and_risk_gate():
    with pytest.raises(PermissionError):
        require_pair(BacklogState.QUEUED,BacklogState.APPROVED,
                     RunState.QUEUED,RunState.APPROVED)
    with pytest.raises(PermissionError):
        require_pair(BacklogState.EVALUATED,BacklogState.APPROVED,
                     RunState.EVALUATING,RunState.APPROVED)
    with pytest.raises(PermissionError):
        require_pair(BacklogState.APPROVED,BacklogState.QUEUED,
                     RunState.COMPLETED,RunState.QUEUED)


def test_audit_append_only_and_transactional_rollback(lab):
    item=enter(lab,schedule(lab))
    for command in ("UPDATE platform_orchestration_events SET reason='tampered'",
                    "DELETE FROM platform_orchestration_events"):
        with pytest.raises(DBAPIError):
            with lab.engine.begin() as db:
                db.execute(text(command))
    before=count(lab)
    with lab.engine.begin() as db:
        db.execute(text("CREATE TRIGGER reject_queue_approved BEFORE INSERT ON platform_orchestration_events "
                        "WHEN NEW.kind='run_transition' BEGIN SELECT RAISE(ABORT,'synthetic audit outage'); END"))
    approved(lab)
    with pytest.raises(DBAPIError):
        lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                             item_id=item.identity.item_id,inputs=input_for(item),
                             reason="rollback all queue steps")
    assert count(lab)==before
    status=lab.service.queue(access_token="admin",strategy_id=lab.strategy)[0]
    assert status.backlog is BacklogState.QUEUED


def test_owner_isolation_and_queue_controls(lab):
    item=enter(lab,schedule(lab))
    with pytest.raises(PermissionError):
        lab.service.queue(access_token="outside",strategy_id=lab.strategy)
    with pytest.raises(PermissionError):
        lab.service.alerts(access_token="outside",strategy_id=lab.strategy)
    with pytest.raises(PermissionError):
        lab.service.materialize_due(access_token="no-token",strategy_id=lab.strategy,
                                    clock=datetime.now(UTC),reason="anonymous")
    lab.service.cancel(access_token="admin",strategy_id=lab.strategy,
                       item_id=item.identity.item_id,reason="cancel preview")
    assert lab.service.queue(access_token="admin",strategy_id=lab.strategy)[0].backlog is BacklogState.CANCELLED
    with pytest.raises(PermissionError):
        lab.service.cancel(access_token="admin",strategy_id=lab.strategy,
                           item_id=item.identity.item_id,reason="cannot cancel again")


def test_stale_after_queue_before_risk_is_skipped_without_phase4_allow(lab):
    now=datetime.now(UTC)
    spec=schedule(lab,kind=ScheduleKind.DELAYED,
                  at=now-timedelta(minutes=10),age=60)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),
                         reason="historical due")
    first=lab.service.materialize_due(access_token="admin",
             strategy_id=lab.strategy,clock=spec.start_at_utc+timedelta(seconds=1),
             reason="synthetic earlier queue creation")
    assert first[0].backlog is BacklogState.QUEUED
    evaluated=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                    item_id=first[0].identity.item_id,
                                    inputs=input_for(first[0]),reason="late evaluation")
    assert evaluated.backlog is BacklogState.SKIPPED
    assert evaluated.run_state is RunState.PAUSED
    assert evaluated.execution_authorized is False
    assert count(lab,"gate_binding")==0
    assert any(x.kind=="stale_item" for x in
               lab.service.alerts(access_token="admin",strategy_id=lab.strategy))
    assert not lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                                  item_id=first[0].identity.item_id,
                                  inputs=input_for(first[0]),reason="stale requeue denied")
    assert count(lab,"requeue_denied")==1


def test_operator_pause_item_requires_fresh_audited_requeue(lab):
    approved(lab)
    item=enter(lab,schedule(lab))
    paused=lab.service.pause_item(access_token="operator",strategy_id=lab.strategy,
                                  item_id=item.identity.item_id,reason="operator pause")
    assert paused.backlog is BacklogState.PAUSED
    assert paused.run_state is RunState.PAUSED
    assert any(x.reason_code=="OPERATOR_PAUSED" for x in
               lab.service.alerts(access_token="admin",strategy_id=lab.strategy))
    with pytest.raises(PermissionError):
        lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                             item_id=item.identity.item_id,inputs=input_for(item),
                             reason="cannot skip requeue")
    assert lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                               item_id=item.identity.item_id,inputs=input_for(item),
                               reason="risk verified resume")
    assert lab.service.queue(access_token="admin",strategy_id=lab.strategy)[0].backlog is BacklogState.QUEUED


def test_gate_paused_at_materialization_stays_paused_and_requires_requeue(lab):
    approved(lab)
    lab.gate.transition(access_token="admin",strategy_id=lab.strategy,
                        new_state=GateState.PAUSED,approval_mode=ApprovalMode.BLOCKED,
                        reason="offline pause")
    item=enter(lab,schedule(lab))
    assert item.backlog is BacklogState.PAUSED
    assert item.run_state is RunState.PAUSED
    assert lab.service.requeue(access_token="admin",strategy_id=lab.strategy,
                                item_id=item.identity.item_id,inputs=input_for(item),
                                reason="still paused") is False
    assert count(lab, "requeue_denied") == 1


def test_queue_growth_produces_pending_only_alert_event(lab):
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),
                         reason="queue growth test")
    items=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                          clock=datetime.now(UTC),reason="due",backlog_warn_at=1)
    assert len(items)==1 and items[0].backlog is BacklogState.QUEUED
    alerts=lab.service.alerts(access_token="admin",strategy_id=lab.strategy)
    assert len(alerts)==1 and alerts[0].kind=="queue_growth"
    assert alerts[0].delivery=="pending"
    assert alerts[0].execution_authorized is False

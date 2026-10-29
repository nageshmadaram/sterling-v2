"""Phase 4 isolated append-only offline gate integration. No broker connections."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.execution_validation import (
    ApprovalMode, Decision, ExecutionMode, GateInput, GateState, RiskEvidence,
    RiskPolicy, SignalIntent, StrategyHealth, evaluate_offline,
)
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.persistence import strategy_registry_service as registry

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 4, tzinfo=UTC)

@pytest.fixture()
def world(tmp_path, monkeypatch):
    from app.core.config import settings
    url = f"sqlite:///{tmp_path / 'phase4_gate.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(config, "head")
    engine = make_engine(url)
    def verified(token):
        if token == "admin":
            return SimpleNamespace(user_id="owner", role="admin", view=None, owner_uid=None)
        if token == "operator":
            return SimpleNamespace(user_id="owner", role="operator", view=None, owner_uid=None)
        if token == "foreign":
            return SimpleNamespace(user_id="different", role="operator", view=None, owner_uid=None)
        raise PermissionError("signed credential required")
    monkeypatch.setattr(registry, "_identity", verified)
    reg = registry.StrategyRegistryService(make_session_factory(engine))
    key = reg.create_strategy(access_token="admin", slug="phase4", display_name="Safety gate",
                              reason="validation synthetic setup")
    version = reg.publish_version(access_token="admin", strategy_id=key,
                                  version="v1", parameters={"window": 12},
                                  reason="immutable synthetic baseline")
    run = reg.create_offline_run(access_token="admin", version_id=version,
                                 mode="simulation", reason="offline run")
    gate = OfflineExecutionGate(make_session_factory(engine))
    try:
        yield engine, reg, gate, key, version, run
    finally:
        engine.dispose()


def input_for(strategy_id, version_id, run_id, **overrides):
    values = dict(
        signal=SignalIntent(
            signal_id="s-1", order_intent_id="intent-1", strategy_id=strategy_id,
            version_id=version_id, run_id=run_id, qualified_symbol="NSE:INFY",
            mode=ExecutionMode.SIMULATION, units=Decimal(5),
            estimated_notional_inr=Decimal(5000),
        ),
        policy=RiskPolicy(max_exposure_inr=100000, max_daily_loss_inr=3000,
                          max_session_loss_inr=2000, max_order_notional_inr=15000,
                          max_units=100),
        health=StrategyHealth(
            valid_parameters=True, has_risk_profile=True, valid_market_access=True,
            recent_performance=Decimal("0.02"), configuration_at_utc=NOW, operational=True,
        ),
        exposure=RiskEvidence(
            current_exposure_inr=10000, daily_loss_inr=100,
            session_loss_inr=100, provider_healthy=True,
            market_open=True, risk_profile_verified=True,
            symbol_valid=True, source_verified=True,
        ),
        market_quality_status="good", market_quality_score=98, market_regime="trending",
        at_utc=NOW,
    )
    values.update(overrides)
    return GateInput(**values)


def setup_approved(gate, key, approval_mode=ApprovalMode.AUTO):
    gate.transition(access_token="admin", strategy_id=None,
                    new_state=GateState.VALIDATION_REQUIRED,
                    approval_mode=ApprovalMode.BLOCKED, reason="system offline review")
    gate.transition(access_token="admin", strategy_id=None, new_state=GateState.APPROVED,
                    approval_mode=ApprovalMode.MANUAL, reason="offline system reviewed")
    gate.transition(access_token="admin", strategy_id=key,
                    new_state=GateState.VALIDATION_REQUIRED,
                    approval_mode=ApprovalMode.BLOCKED, reason="strategy validation")
    gate.transition(access_token="admin", strategy_id=key, new_state=GateState.APPROVED,
                    approval_mode=approval_mode, reason="offline strategy validated")


def count(engine, kind=None):
    with engine.connect() as conn:
        if kind:
            return conn.execute(text("SELECT COUNT(*) FROM platform_gate_events WHERE kind=:k"), {"k":kind}).scalar_one()
        return conn.execute(text("SELECT COUNT(*) FROM platform_gate_events")).scalar_one()


def test_default_disabled_deferred_and_audited(world):
    engine, _, gate, key, version, run = world
    result=gate.dry_run(access_token="admin", inputs=input_for(key,version,run), reason="evaluate default")
    assert result.decision == Decision.DEFER and result.codes == ("VALIDATION_REQUIRED",)
    assert not result.execution_authorized
    assert count(engine, "signal_received") == 1
    assert count(engine, "gate_evaluated") == 1
    assert count(engine, "risk_decision") == 1
    assert count(engine, "approval_decision") == 1
    assert count(engine) == 4


def test_simulation_auto_allow_has_no_broker_authority(world):
    engine, _, gate, key, version, run=world
    setup_approved(gate,key)
    result=gate.dry_run(access_token="admin", inputs=input_for(key,version,run),
                        reason="evaluate offline ready")
    assert result.decision == Decision.ALLOW
    assert result.execution_authorized is False
    assert count(engine, "risk_decision") == 1
    with engine.connect() as c:
        audit=c.execute(text("SELECT run_id, version_id, actor_id, payload_json FROM platform_gate_events WHERE id=:id"),
                        {"id":result.decision_event_id}).one()
    assert audit[0] == run and audit[1] == version and audit[2] == "owner"
    assert '"execution_authorized":false' in audit[3]


def test_live_always_rejected_even_under_approved_gate(world):
    _, _, gate, key, version, run=world
    setup_approved(gate,key)
    inputs=input_for(key,version,run,signal=input_for(key,version,run).signal.model_copy(
        update={"mode":ExecutionMode.LIVE}))
    out=gate.dry_run(access_token="admin",inputs=inputs, reason="prove live blocked")
    assert out.decision == Decision.REJECT and out.codes == ("LIVE_DISABLED",)
    assert not out.execution_authorized
    with pytest.raises(PermissionError):
        gate.approve_offline_signal(access_token="admin",inputs=inputs,reason="not live")


def test_manual_approval_uses_signed_actor_and_exact_one_time_signal(world):
    _, _, gate, key, version, run=world
    setup_approved(gate,key,ApprovalMode.MANUAL)
    values=input_for(key,version,run)
    first=gate.dry_run(access_token="admin",inputs=values,reason="no operator approval")
    assert first.decision == Decision.DEFER
    with pytest.raises(PermissionError):
        gate.approve_offline_signal(access_token="viewer",inputs=values,reason="invalid actor")
    gate.approve_offline_signal(access_token="operator",inputs=values,reason="approve dry-run only")
    second=gate.dry_run(access_token="admin",inputs=values,reason="operator-approved validation")
    assert second.decision == Decision.ALLOW
    third=gate.dry_run(access_token="admin",inputs=values,reason="replay denied")
    assert third.decision == Decision.DEFER


def test_paper_auto_must_defer_and_blocked_policy_rejects(world):
    _, _, gate, key, version, run=world
    setup_approved(gate,key,ApprovalMode.AUTO)
    signal=input_for(key,version,run).signal.model_copy(update={"mode":ExecutionMode.PAPER})
    result=gate.dry_run(access_token="admin",inputs=input_for(key,version,run,signal=signal),
                        reason="paper preview no auto")
    assert result.decision == Decision.DEFER and "PAPER_OPERATOR_APPROVAL_REQUIRED" in result.codes
    with pytest.raises(PermissionError):
        gate.transition(access_token="viewer",strategy_id=key,
                        new_state=GateState.EMERGENCY_STOP,
                        approval_mode=ApprovalMode.BLOCKED,reason="no authority")


@pytest.mark.parametrize("change,code", [
    ({"market_quality_status":"degraded","market_quality_score":30},"MARKET_DATA_UNSAFE"),
    ({"market_regime":"low_liquidity"},"REGIME_BLOCKED"),
    ({"health": StrategyHealth(valid_parameters=False,has_risk_profile=True,
              valid_market_access=True,recent_performance=0,configuration_at_utc=NOW,
              operational=True)},"STRATEGY_UNHEALTHY"),
    ({"health": StrategyHealth(valid_parameters=True,has_risk_profile=True,
              valid_market_access=True,recent_performance=0,
              configuration_at_utc=NOW-timedelta(days=4),operational=True)},"CONFIG_STALE"),
    ({"exposure": RiskEvidence(current_exposure_inr=100000,daily_loss_inr=0,
         session_loss_inr=0,provider_healthy=True,market_open=True,
         risk_profile_verified=True,symbol_valid=True,source_verified=True)},"EXPOSURE_CAP"),
    ({"exposure": RiskEvidence(current_exposure_inr=0,daily_loss_inr=3000,
         session_loss_inr=0,provider_healthy=True,market_open=True,
         risk_profile_verified=True,symbol_valid=True,source_verified=True)},"DAILY_LOSS_CAP"),
])
def test_risk_checks_fail_closed(change,code):
    inputs=input_for("s","v","r",**change)
    decision,codes=evaluate_offline(inputs,state=GateState.APPROVED,
        system_state=GateState.APPROVED,approval_mode=ApprovalMode.AUTO,human_approval=False)
    assert code in codes and decision != Decision.ALLOW


def test_quality_failure_triggers_audited_one_way_emergency_stop(world):
    engine, _, gate, key, version, run=world
    setup_approved(gate,key)
    degraded=input_for(key,version,run,market_quality_status="degraded",market_quality_score=25)
    result=gate.dry_run(access_token="admin",inputs=degraded,reason="unsafe market data")
    assert result.decision == Decision.EMERGENCY_STOP
    assert count(engine, "state_transition") == 5
    with engine.connect() as c:
        tail=c.execute(text("SELECT payload_json FROM platform_gate_events WHERE kind='state_transition' ORDER BY occurred_at_ms DESC, sequence DESC LIMIT 1")).scalar_one()
    assert '"alert_required":true' in tail and '"alert_delivery":"pending"' in tail
    with pytest.raises(PermissionError):
        gate.transition(access_token="operator",strategy_id=key,
                        new_state=GateState.VALIDATION_REQUIRED,
                        approval_mode=ApprovalMode.BLOCKED,reason="incident-reviewed: no admin",
                        explicit_emergency_reset=True)
    with pytest.raises(PermissionError):
        gate.transition(access_token="admin",strategy_id=key,
                        new_state=GateState.VALIDATION_REQUIRED,
                        approval_mode=ApprovalMode.BLOCKED,reason="unreviewed",
                        explicit_emergency_reset=True)
    gate.transition(access_token="admin",strategy_id=key,
                    new_state=GateState.VALIDATION_REQUIRED,
                    approval_mode=ApprovalMode.BLOCKED,reason="incident-reviewed: simulated proof only",
                    explicit_emergency_reset=True)


def test_strategy_scope_and_version_run_binding_refused(world):
    engine, _, gate, key, version, run=world
    with pytest.raises(PermissionError):
        gate.dry_run(access_token="foreign",inputs=input_for(key,version,run),
                     reason="foreign tenant")
    with pytest.raises(LookupError):
        gate.dry_run(access_token="admin",inputs=input_for(key,"nonexistent",run),
                     reason="missing version")
    with pytest.raises(PermissionError):
        gate.dry_run(access_token="admin",inputs=input_for(key,version,"wrong-run"),
                     reason="missing run")
    assert count(engine)==0


def test_database_protects_gate_event_mutability(world):
    engine, _, gate, key, version, run=world
    gate.dry_run(access_token="admin",inputs=input_for(key,version,run),reason="audit")
    for sql in ("UPDATE platform_gate_events SET reason='tampered'",
                "DELETE FROM platform_gate_events"):
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text(sql))


def test_forced_audit_insert_failure_rolls_back_whole_decision(world):
    engine, _, gate, key, version, run=world
    with engine.begin() as c:
        c.execute(text("CREATE TRIGGER test_fail_gate_insert BEFORE INSERT ON platform_gate_events "
                       "WHEN NEW.kind='risk_decision' BEGIN SELECT RAISE(ABORT,'simulated audit outage'); END"))
    with pytest.raises(DBAPIError):
        gate.dry_run(access_token="admin",inputs=input_for(key,version,run),
                     reason="should roll back")
    assert count(engine)==0


def test_locked_strategy_is_audited_rejection(world):
    _, reg, gate, key, version, run=world
    reg.lock_strategy(access_token="admin",strategy_id=key,reason="freeze strategy")
    response=gate.dry_run(access_token="admin",inputs=input_for(key,version,run),
                          reason="locked cannot run")
    assert response.decision == Decision.REJECT
    assert "STRATEGY_LOCKED_OR_RETIRED" in response.codes
    assert not response.execution_authorized


def test_system_wide_kill_switch_blocks_every_offline_strategy_and_exposes_pending_alert(world):
    _, _, gate, key, version, run=world
    setup_approved(gate,key)
    gate.transition(access_token="admin",strategy_id=None,
                    new_state=GateState.EMERGENCY_STOP,
                    approval_mode=ApprovalMode.BLOCKED,reason="system-wide synthetic safety drill")
    result=gate.dry_run(access_token="admin",inputs=input_for(key,version,run),
                        reason="system kill drill")
    assert result.decision == Decision.EMERGENCY_STOP
    assert not result.execution_authorized
    outbox=gate.pending_alerts(access_token="admin",strategy_id=None)
    assert len(outbox) == 1 and outbox[0]["alert_delivery"] == "pending"
    assert outbox[0]["execution_authorized"] is False
    with pytest.raises(PermissionError):
        gate.pending_alerts(access_token="operator",strategy_id=None)
    strategy_alerts=gate.pending_alerts(access_token="operator",strategy_id=key)
    assert len(strategy_alerts) == 1
    with pytest.raises(PermissionError):
        gate.pending_alerts(access_token="foreign",strategy_id=key)

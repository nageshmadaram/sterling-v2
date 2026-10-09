from types import SimpleNamespace

import pytest

from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)


def _request(**over):
    base = dict(
        uid="owner",
        account_id="KITE-APP-1",
        strategy_id="sterling-kite",
        generation_id="gen-1",
        signal_id="sig-1",
        exchange="NFO",
        symbol="NIFTY26OCT25000CE",
        side="BUY",
        quantity=75,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type="market_order",
        capital_required=7500.0,
        available_capital=100000.0,
        payload={"product": "NRML"},
    )
    base.update(over)
    return ExecutionRequest(**base)


def _approval(req):
    return RiskApproval(
        approval_id="risk-1",
        uid=req.uid,
        account_id=req.account_id,
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        generation_id=req.generation_id,
        approved=True,
        timestamp_ms=0,
        available_capital=100000.0,
        capital_required=7500.0,
        strategy_id=req.strategy_id,
        signal_id=req.signal_id,
    )


def _binding(**over):
    base = dict(
        binding_id="zerodha-ab1234-20261009",
        client_id="AB1234",
        deployment_id="prod-1",
        live_ready=True,
        verification_account_id="KITE-APP-1",
        verification_profile_client_id="AB1234",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _running_deployment(monkeypatch):
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-1")


@pytest.mark.asyncio
async def test_production_binding_gate_refuses_when_no_active_binding(monkeypatch):
    import app.services.account_binding_service as bindings

    monkeypatch.setattr(bindings, "active_binding", lambda: None)
    ok, reason = await CanonicalExecutionService().verify_production_account_binding(
        SimpleNamespace(_account_id="KITE-APP-1", _kite_user_id="AB1234"),
        _request(),
    )
    assert ok is False
    assert "No ACTIVE broker account binding" in reason


@pytest.mark.asyncio
async def test_production_binding_gate_accepts_exact_current_identity(monkeypatch):
    import app.services.account_binding_service as bindings

    binding = _binding()
    monkeypatch.setattr(bindings, "active_binding", lambda: binding)
    monkeypatch.setattr(bindings, "verification_is_current", lambda value: value is binding)

    ok, reason = await CanonicalExecutionService().verify_production_account_binding(
        SimpleNamespace(_account_id="KITE-APP-1", _kite_user_id="AB1234"),
        _request(),
    )
    assert ok is True
    assert "verified production broker binding" in reason


@pytest.mark.asyncio
async def test_stale_binding_is_refreshed_read_only_before_entry(monkeypatch):
    import app.services.account_binding_service as bindings
    import app.services.broker_binding_verification as verifier

    stale = _binding()
    fresh = _binding()
    monkeypatch.setattr(bindings, "active_binding", lambda: stale)
    monkeypatch.setattr(bindings, "verification_is_current", lambda value: value is fresh)

    calls = []

    async def refresh(binding_id, **kwargs):
        calls.append((binding_id, kwargs))
        return fresh

    monkeypatch.setattr(verifier, "verify_current_kite_binding", refresh)

    ok, _ = await CanonicalExecutionService().verify_production_account_binding(
        SimpleNamespace(_account_id="KITE-APP-1", _kite_user_id="AB1234"),
        _request(),
    )
    assert ok is True
    assert calls == [(
        stale.binding_id,
        {"user_id": "owner", "application_account_id": "KITE-APP-1"},
    )]


@pytest.mark.asyncio
async def test_binding_gate_refuses_account_or_broker_user_mismatch(monkeypatch):
    import app.services.account_binding_service as bindings

    binding = _binding()
    monkeypatch.setattr(bindings, "active_binding", lambda: binding)
    monkeypatch.setattr(bindings, "verification_is_current", lambda _value: True)
    service = CanonicalExecutionService()

    ok, reason = await service.verify_production_account_binding(
        SimpleNamespace(_account_id="OTHER", _kite_user_id="AB1234"),
        _request(),
    )
    assert ok is False and "application account" in reason

    ok, reason = await service.verify_production_account_binding(
        SimpleNamespace(_account_id="KITE-APP-1", _kite_user_id="WRONG"),
        _request(),
    )
    assert ok is False and "does not match" in reason



@pytest.mark.asyncio
async def test_binding_gate_refuses_wrong_or_missing_running_deployment(monkeypatch):
    import app.services.account_binding_service as bindings

    binding = _binding()
    monkeypatch.setattr(bindings, "active_binding", lambda: binding)
    monkeypatch.setattr(bindings, "verification_is_current", lambda _value: True)
    service = CanonicalExecutionService()
    client = SimpleNamespace(_account_id="KITE-APP-1", _kite_user_id="AB1234")

    monkeypatch.delenv("STERLING_DEPLOYMENT_ID", raising=False)
    ok, reason = await service.verify_production_account_binding(client, _request())
    assert ok is False and "STERLING_DEPLOYMENT_ID is required" in reason

    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-2")
    ok, reason = await service.verify_production_account_binding(client, _request())
    assert ok is False and "does not match" in reason


@pytest.mark.asyncio
async def test_production_live_entry_cannot_reach_later_gates_without_binding(monkeypatch):
    import app.core.runtime_env as runtime_env
    import app.services.account_binding_service as bindings

    monkeypatch.setattr(runtime_env, "is_production", lambda: True)
    monkeypatch.setattr(bindings, "active_binding", lambda: None)

    req = _request()
    client = SimpleNamespace(
        _is_paper=False,
        _account_id=req.account_id,
        _kite_user_id="AB1234",
    )
    result = await CanonicalExecutionService()._submit_order(
        req,
        broker_client=client,
        risk_approval=_approval(req),
    )

    assert result.success is False
    assert result.status == "REJECTED_ACCOUNT_BINDING"
    assert "ACTIVE broker account binding" in result.error

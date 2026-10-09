"""Canonical live-entry enforcement for durable broker account bindings.

The account binding is a real safety authority, not a dashboard/certification
decoration. Every real broker exposure increase must prove the ACTIVE binding
before the canonical executor can touch the account. Reductions remain
available even when binding metadata is absent or stale.
"""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import db
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)


class Broker:
    def __init__(self, account_id: str, *, paper: bool = False, kite_user: str = "KU1", net=()):
        self._account_id = account_id
        self._is_paper = paper
        self._kite_user_id = kite_user
        self.calls = 0
        self._net = list(net)

    async def get_positions_raw(self):
        return {"net": list(self._net), "day": []}

    async def place_order(self, **_kwargs):
        self.calls += 1
        return {"order_id": f"ORD-{self.calls}"}


@pytest.fixture
def live_identity(monkeypatch):
    """A current deployment/egress observation for tests of the real binding helper."""
    from app.services import continuity_doctor

    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-1")
    monkeypatch.setattr(
        continuity_doctor,
        "egress_status",
        lambda: SimpleNamespace(verified=True, reason="verified test egress"),
    )


def entry(tag: str = "binding"):
    uid = f"u-{tag}"
    account = f"acct-{tag}"
    request = ExecutionRequest(
        uid=uid,
        account_id=account,
        strategy_id="sterling-kite",
        generation_id="g1",
        signal_id=f"sig-{tag}",
        exchange="NFO",
        symbol="NIFTY26OCT25000CE",
        side="BUY",
        quantity=75,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type="LIMIT",
        price=100.0,
        payload={"product": "NRML"},
    )
    approval = RiskApproval(
        approval_id=f"ap-{tag}",
        uid=uid,
        account_id=account,
        symbol=request.symbol,
        side="BUY",
        quantity=75,
        generation_id="g1",
        approved=True,
        available_capital=500_000.0,
        capital_required=7_500.0,
        strategy_id=request.strategy_id,
        signal_id=request.signal_id,
    )
    return request, approval


@pytest.mark.asyncio
async def test_live_entry_checks_binding_even_when_environment_is_development(
    monkeypatch,
):
    """Real transport decides the safety requirement; ENVIRONMENT cannot weaken it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "development", raising=False)
    request, approval = entry("live")
    broker = Broker(request.account_id)
    gate = AsyncMock(return_value=(False, "No ACTIVE broker account binding is declared"))
    monkeypatch.setattr(
        CanonicalExecutionService,
        "verify_live_account_binding",
        gate,
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is False
    assert result.status == "REJECTED_ACCOUNT_BINDING"
    assert "No ACTIVE broker account binding" in result.error
    gate.assert_awaited_once()
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_explicit_test_double_entry_does_not_require_live_account_binding(monkeypatch):
    request, approval = entry("double")
    broker = Broker(request.account_id, paper=True)
    gate = AsyncMock(side_effect=AssertionError("test-double entry consulted live binding"))
    monkeypatch.setattr(
        CanonicalExecutionService,
        "verify_live_account_binding",
        gate,
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert result.success is True, result.error
    gate.assert_not_awaited()
    assert broker.calls == 1


@pytest.mark.asyncio
async def test_live_exit_never_depends_on_account_binding(monkeypatch):
    uid, account, symbol = "u-exit-binding", "acct-exit-binding", "SBIN"
    db.set_inventory(
        account_id=account,
        uid=uid,
        symbol=symbol,
        net_quantity=20,
        exchange="NSE",
    )
    broker = Broker(
        account,
        net=[{
            "tradingsymbol": symbol,
            "exchange": "NSE",
            "product": "NRML",
            "quantity": 20,
        }],
    )
    gate = AsyncMock(side_effect=AssertionError("exit consulted entry binding gate"))
    monkeypatch.setattr(
        CanonicalExecutionService,
        "verify_live_account_binding",
        gate,
    )
    request = ExecutionRequest(
        uid=uid,
        account_id=account,
        strategy_id="sterling-kite",
        generation_id="g1",
        signal_id="exit-1",
        exchange="NSE",
        symbol=symbol,
        side="SELL",
        quantity=20,
        exposure_effect=ExposureEffect.CLOSE_POSITION,
        payload={"product": "NRML"},
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker
    )

    assert result.success is True, result.error
    gate.assert_not_awaited()
    assert broker.calls == 1


@pytest.mark.asyncio
async def test_real_binding_gate_accepts_exact_current_identity(
    monkeypatch,
    _default_live_binding_verifier,
    live_identity,
):
    """Exercise the real helper, bypassing the suite's default binding stub."""
    from app.services import account_binding_service as bindings

    binding = SimpleNamespace(
        binding_id="zerodha-ku1-20261009",
        live_ready=True,
        client_id="KU1",
        deployment_id="prod-1",
        verification_account_id="acct-exact",
        verification_profile_client_id="KU1",
    )
    monkeypatch.setattr(bindings, "active_binding", lambda: binding)
    monkeypatch.setattr(bindings, "verification_is_current", lambda candidate: candidate is binding)

    request, _approval = entry("exact")
    request = replace(request, account_id="acct-exact")
    broker = Broker("acct-exact", kite_user="KU1")

    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )

    assert ok is True
    assert "verified" in reason


@pytest.mark.asyncio
async def test_real_binding_gate_rejects_missing_or_wrong_identity(
    monkeypatch,
    _default_live_binding_verifier,
    live_identity,
):
    from app.services import account_binding_service as bindings

    request, _approval = entry("wrong")
    broker = Broker(request.account_id, kite_user="KU1")

    monkeypatch.setattr(bindings, "active_binding", lambda: None)
    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )
    assert ok is False
    assert "No ACTIVE" in reason

    wrong = SimpleNamespace(
        binding_id="zerodha-ku2-20261009",
        live_ready=True,
        client_id="KU2",
        deployment_id="prod-1",
        verification_account_id=request.account_id,
        verification_profile_client_id="KU2",
    )
    monkeypatch.setattr(bindings, "active_binding", lambda: wrong)
    monkeypatch.setattr(bindings, "verification_is_current", lambda _candidate: True)

    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )
    assert ok is False
    assert "does not match ACTIVE binding client" in reason


@pytest.mark.asyncio
async def test_stale_binding_is_refreshed_read_only_before_entry(
    monkeypatch,
    _default_live_binding_verifier,
    live_identity,
):
    from app.services import account_binding_service as bindings
    from app.services import broker_binding_verification as verifier

    stale = SimpleNamespace(
        binding_id="zerodha-ku1-20261009",
        live_ready=True,
        client_id="KU1",
        deployment_id="prod-1",
        verification_account_id="",
        verification_profile_client_id="",
    )
    fresh = SimpleNamespace(
        binding_id=stale.binding_id,
        live_ready=True,
        client_id="KU1",
        deployment_id="prod-1",
        verification_account_id="acct-refresh",
        verification_profile_client_id="KU1",
    )
    monkeypatch.setattr(bindings, "active_binding", lambda: stale)
    monkeypatch.setattr(
        bindings,
        "verification_is_current",
        lambda candidate: candidate is fresh,
    )
    refresh = AsyncMock(return_value=fresh)
    monkeypatch.setattr(verifier, "verify_current_kite_binding", refresh)

    request, _approval = entry("refresh")
    request = replace(request, account_id="acct-refresh")
    broker = Broker("acct-refresh", kite_user="KU1")

    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )

    assert ok is True, reason
    refresh.assert_awaited_once_with(
        stale.binding_id,
        user_id=request.uid,
        application_account_id=request.account_id,
    )


@pytest.mark.asyncio
async def test_real_binding_gate_rejects_unverified_static_egress(
    monkeypatch,
    _default_live_binding_verifier,
):
    from app.services import continuity_doctor

    monkeypatch.setattr(
        continuity_doctor,
        "egress_status",
        lambda: SimpleNamespace(
            verified=False,
            reason="outbound address does not match registered static IP",
        ),
    )
    request, _approval = entry("egress")
    broker = Broker(request.account_id)

    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )

    assert ok is False
    assert "Static egress is not verified" in reason


@pytest.mark.asyncio
async def test_real_binding_gate_rejects_wrong_running_deployment(
    monkeypatch,
    _default_live_binding_verifier,
    live_identity,
):
    from app.services import account_binding_service as bindings

    binding = SimpleNamespace(
        binding_id="zerodha-ku1-20261009",
        live_ready=True,
        client_id="KU1",
        deployment_id="prod-2",
        verification_account_id="acct-deploy",
        verification_profile_client_id="KU1",
    )
    monkeypatch.setattr(bindings, "active_binding", lambda: binding)
    monkeypatch.setattr(bindings, "verification_is_current", lambda _candidate: True)
    request, _approval = entry("deploy")
    request = replace(request, account_id="acct-deploy")
    broker = Broker("acct-deploy", kite_user="KU1")

    ok, reason = await _default_live_binding_verifier(
        CanonicalExecutionService(), broker, request
    )

    assert ok is False
    assert "does not match ACTIVE binding deployment" in reason

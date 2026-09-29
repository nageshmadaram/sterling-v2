from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import intraday_canonical_entry as bridge
from app.services.execution_service import ExecutionResult


@pytest.mark.asyncio
async def test_missing_authenticated_account_identity_fails_closed():
    client = SimpleNamespace(_is_paper=False)

    result = await bridge.submit_entry(
        client=client, uid="u1", signal_id="sig-1", sub_strategy="pivot_break",
        exchange="NFO", symbol="NIFTYOPT", quantity=75, limit_price=100.0,
    )

    assert result.success is False
    assert result.status == "REJECTED"
    assert "account identity" in result.error.lower()


@pytest.mark.asyncio
async def test_missing_fresh_broker_capital_fails_closed(monkeypatch):
    client = SimpleNamespace(_is_paper=False, _account_id="acct-1")

    async def no_capital(_client):
        return 0.0

    monkeypatch.setattr(
        "app.services.kite_engine.service.available_fo_capital", no_capital
    )

    result = await bridge.submit_entry(
        client=client, uid="u1", signal_id="sig-1", sub_strategy="pivot_break",
        exchange="NFO", symbol="NIFTYOPT", quantity=75, limit_price=100.0,
    )

    assert result.success is False
    assert result.status == "REJECTED"
    assert "capital" in result.error.lower()


@pytest.mark.asyncio
async def test_request_and_risk_proof_are_bound_to_same_intraday_intent(monkeypatch):
    client = SimpleNamespace(_is_paper=False, _account_id="acct-1")
    captured = {}

    async def capital(_client):
        return 500_000.0

    async def submit(self, request, broker_client=None, risk_approval=None, **_kwargs):
        captured["request"] = request
        captured["client"] = broker_client
        captured["approval"] = risk_approval
        return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id="OID-1")

    monkeypatch.setattr(
        "app.services.kite_engine.service.available_fo_capital", capital
    )
    monkeypatch.setattr(bridge.CanonicalExecutionService, "submit_order", submit)

    result = await bridge.submit_entry(
        client=client, uid="u1", signal_id="sig-1", sub_strategy="pivot_break",
        exchange="NFO", symbol="NIFTYOPT", quantity=75, limit_price=100.0,
    )

    assert result.success is True
    request = captured["request"]
    approval = captured["approval"]
    assert captured["client"] is client
    assert request.account_id == approval.account_id == "acct-1"
    assert request.strategy_id == approval.strategy_id == "intraday"
    assert request.signal_id == approval.signal_id == "sig-1"
    assert request.generation_id == approval.generation_id == "A400.1:pivot_break"
    assert request.quantity == approval.quantity == 75
    assert request.side == approval.side == "BUY"
    assert request.price == 100.0
    assert request.payload["product"] == "MIS"
    assert request.payload["sub_strategy"] == "pivot_break"
    assert request.capital_required == approval.capital_required == 7_500.0
    assert approval.available_capital == 500_000.0

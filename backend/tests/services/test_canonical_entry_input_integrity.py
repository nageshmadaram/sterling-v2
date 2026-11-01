"""Offline canonical-entry fail-closed input validation.

A rejected entry must never invoke the order journal or broker transport.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import canonical_strategy_entry as bridge
from app.services.execution_service import ExecutionResult, ExposureEffect


def _params(**overrides):
    params = dict(
        client=SimpleNamespace(_account_id="PAPER-ACCOUNT", _is_paper=True),
        uid="synthetic-operator",
        strategy_id="synthetic",
        generation_id="gen-1",
        signal_id="sig-1",
        exchange="NFO",
        symbol="TEST26NOV100CE",
        quantity=2,
        capital_price=50.0,
    )
    params.update(overrides)
    return params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quantity", "price"),
    [(0, 50), (-1, 50), (True, 50), (1.2, 50), ("2", 50),
     (2, 0), (2, -3), (2, math.nan), (2, math.inf),
     (2, -math.inf), (2, "bad"), (2, 1e308)],
)
async def test_bad_quantity_price_or_required_capital_never_submits(
    monkeypatch, quantity, price
):
    submit = AsyncMock()
    monkeypatch.setattr(bridge.CanonicalExecutionService, "submit_order", submit)
    capital = AsyncMock(return_value=100_000.0)
    monkeypatch.setattr(bridge, "available_entry_capital", capital)
    result = await bridge.submit_entry(**_params(quantity=quantity, capital_price=price))
    assert result.status == "REJECTED"
    assert not result.success
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_infinite_broker_capital_fails_closed(monkeypatch):
    from app.services.kite_engine import service
    monkeypatch.setattr(service, "available_fo_capital", AsyncMock(return_value=math.inf))
    client = SimpleNamespace(_is_paper=False)
    assert await bridge.available_entry_capital(client) == 0.0


@pytest.mark.asyncio
async def test_nonfinite_paper_balance_fails_closed(monkeypatch):
    from app.services.kite_engine import service
    monkeypatch.setattr(service, "available_fo_capital", AsyncMock(return_value=0.0))
    client = SimpleNamespace(
        _is_paper=True,
        get_balances=AsyncMock(return_value=[
            SimpleNamespace(asset="INR (Equity)", available=math.inf)
        ]),
    )
    assert await bridge.available_entry_capital(client) == 0.0


@pytest.mark.asyncio
async def test_valid_entry_reaches_canonical_service_with_bound_identity(monkeypatch):
    capital = AsyncMock(return_value=500.0)
    monkeypatch.setattr(bridge, "available_entry_capital", capital)
    submitted = AsyncMock(return_value=ExecutionResult(success=True, status="SUBMITTED"))
    monkeypatch.setattr(bridge.CanonicalExecutionService, "submit_order", submitted)
    result = await bridge.submit_entry(**_params())
    assert result.success is True
    submitted.assert_awaited_once()
    request = submitted.await_args.args[0]
    approval = submitted.await_args.kwargs["risk_approval"]
    assert request.account_id == "PAPER-ACCOUNT"
    assert request.exposure_effect is ExposureEffect.INCREASE_EXPOSURE
    assert request.capital_required == 100.0
    assert request.available_capital == 500.0
    assert approval.account_id == request.account_id
    assert approval.quantity == request.quantity

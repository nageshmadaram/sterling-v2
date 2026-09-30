"""Ordinary orders and legacy amendments cannot borrow protective authority."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import lane_registry
from app.services import db
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("effect", [
    ExposureEffect.CANCEL_ORDER,
    ExposureEffect.MODIFY_ORDER,
    ExposureEffect.PROTECT_POSITION,
    "UNKNOWN",
    None,
])
async def test_non_order_effect_never_reaches_order_transport(monkeypatch, effect, side):
    client = SimpleNamespace(_account_id="acct", _is_paper=False, place_order=AsyncMock())
    service = CanonicalExecutionService()
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kw: (True, ""))
    request = ExecutionRequest(
        uid="u", account_id="acct", strategy_id="supertrend", generation_id="g",
        signal_id="s", exchange="NFO", symbol="NIFTY26SEP25000CE", side=side,
        quantity=50, exposure_effect=effect,
    )

    result = await service.submit_order(request, broker_client=client)

    assert result.status == "REJECTED"
    assert "exposure effect" in result.error
    client.place_order.assert_not_awaited()


@pytest.mark.parametrize("promoted", [False, True])
@pytest.mark.parametrize("mode", [False, None, "true", "false", 1, 0])
@pytest.mark.parametrize("changes", [
    {"quantity": 100},
    {"price": 101.0},
    {"order_type": "MARKET"},
    {"quantity": 1, "risk_reducing": True},
])
async def test_generic_modifications_remain_paper_only(monkeypatch, promoted, mode, changes):
    client = SimpleNamespace(_account_id="acct", _is_paper=mode, modify_order=AsyncMock())
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", promoted)

    result = await CanonicalExecutionService().modify_order(
        uid="u", account_id="acct", order_id="ORDER-1", changes=changes,
        broker_client=client,
    )

    assert result.status == "HALTED"
    assert "paper-only" in result.error
    client.modify_order.assert_not_awaited()


async def test_paper_modification_preserves_simulation(monkeypatch):
    client = SimpleNamespace(_account_id="acct", _is_paper=True, modify_order=AsyncMock())
    service = CanonicalExecutionService()
    monkeypatch.setattr(db, "get_execution_control", lambda **kw: {
        "operator_state": "RUNNING", "recovery_state": "CLEAN",
    })
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kw: (True, ""))

    result = await service.modify_order(
        uid="u", account_id="acct", order_id="ORDER-1", changes={"price": 101.0},
        broker_client=client,
    )

    assert result.status == "ACKNOWLEDGED"
    client.modify_order.assert_awaited_once_with(order_id="ORDER-1", price=101.0)


async def test_modification_control_read_failure_stays_closed(monkeypatch):
    client = SimpleNamespace(_account_id="acct", _is_paper=True, modify_order=AsyncMock())
    service = CanonicalExecutionService()

    def unavailable(**kw):
        raise RuntimeError("control database unavailable")

    monkeypatch.setattr(db, "get_execution_control", unavailable)
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kw: (True, ""))

    result = await service.modify_order(
        uid="u", account_id="acct", order_id="ORDER-1", changes={"price": 101.0},
        broker_client=client,
    )

    assert result.status == "HALTED"
    assert "control state unavailable" in result.error
    client.modify_order.assert_not_awaited()


@pytest.mark.parametrize("product", [None, "", "UNKNOWN", False])
async def test_unknown_product_never_defaults_at_submission_or_reduction(product):
    client = SimpleNamespace(_account_id="acct", _is_paper=False,
                             place_order=AsyncMock(), get_positions_raw=AsyncMock(return_value={
                                 "net": [{"tradingsymbol": "OPT", "exchange": "NFO",
                                          "product": "NRML", "quantity": 50}]}))
    request = ExecutionRequest(
        uid="u", account_id="acct", strategy_id="supertrend", generation_id="g",
        signal_id="s", exchange="NFO", symbol="OPT", side="SELL", quantity=50,
        exposure_effect=ExposureEffect.REDUCE_EXPOSURE, payload={"product": product},
    )
    service = CanonicalExecutionService()
    result = await service.submit_order(request, broker_client=client)
    assert result.status == "REJECTED" and "product" in result.error
    allowed, reason = await service.verify_broker_reduction(client, request)
    assert not allowed and "product" in reason
    client.place_order.assert_not_awaited()

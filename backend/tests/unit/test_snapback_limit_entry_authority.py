"""Snapback LIVE entry/hedge must never silently convert invalid LIMIT to MARKET."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from app.services.snapback_live_executor import SnapbackLiveExecutor


def executor():
    canonical = SimpleNamespace(submit_order=AsyncMock(return_value=SimpleNamespace(
        success=True, status="ACKNOWLEDGED",
    )))
    service = SnapbackLiveExecutor(
        uid="operator", plan_store=SimpleNamespace(),
        execution_service=canonical,
        reconciliation_fn=lambda *_: {"clean": True},
        risk_approval_fn=lambda **_: None,
        protection_fn=lambda **_: None,
        revalidate_fn=lambda *_: {"ok": True},
    )
    return service, canonical


def plan():
    return {
        "plan_id": "plan-1", "mode": "LIVE", "account_id": "account",
        "policy_snapshot_hash": "generation", "opportunity_id": "signal-1",
        "option_symbol": "NIFTY_PE", "option_exchange": "NFO",
        "futures_symbol": "NIFTY_FUT", "futures_exchange": "NFO",
        "order_type": "LIMIT", "limit_price": 100.0, "lot_size": 75,
        "hedge_order_type": "LIMIT", "hedge_price_limit": 23000.0,
        "futures_lot_size": 25,
    }


async def submit(service, payload, *, hedge=False, override=None):
    return await service._submit(
        payload,
        symbol="NIFTY_FUT" if hedge else "NIFTY_PE",
        exchange="NFO", side="SELL" if hedge else "BUY",
        quantity=25 if hedge else 75,
        approval=SimpleNamespace(capital_required=7500, available_capital=100000),
        broker_client=SimpleNamespace(_is_paper=False, _account_id="account"),
        price_override=override,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"order_type": "MARKET"},
    {"order_type": "SL"},
    {"order_type": ""},
    {"order_type": "LIMIT", "limit_price": None},
    {"order_type": "LIMIT", "limit_price": 0},
    {"order_type": "LIMIT", "limit_price": -1},
    {"order_type": "LIMIT", "limit_price": float("nan")},
    {"order_type": "LIMIT", "limit_price": float("inf")},
    {"order_type": "LIMIT", "limit_price": "bad"},
])
async def test_live_option_price_or_type_failure_never_calls_canonical_submit(changed):
    service, canonical = executor()
    result = await submit(service, {**plan(), **changed})
    assert not result.success and result.status == "REJECTED_PLAN"
    canonical.submit_order.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,override", [
    ({"hedge_order_type": "MARKET"}, None),
    ({"hedge_order_type": "LIMIT", "hedge_price_limit": 0}, None),
    ({"hedge_order_type": "LIMIT", "hedge_price_limit": "bad"}, None),
    ({"hedge_order_type": "LIMIT"}, float("nan")),
    ({"hedge_order_type": "LIMIT"}, -25.0),
])
async def test_live_hedge_price_failure_never_becomes_market_order(changed, override):
    service, canonical = executor()
    result = await submit(service, {**plan(), **changed}, hedge=True, override=override)
    assert not result.success and result.status == "REJECTED_PLAN"
    canonical.submit_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_live_option_is_limit_only_and_price_capped():
    service, canonical = executor()
    result = await submit(service, plan())
    assert result.success
    canonical.submit_order.assert_awaited_once()
    req = canonical.submit_order.await_args.args[0]
    assert req.order_type == "LIMIT"
    assert req.price == 100
    assert req.quantity == 75
    assert req.payload["limit_price"] == 100


@pytest.mark.asyncio
async def test_valid_live_hedge_uses_fresh_limit_override():
    service, canonical = executor()
    result = await submit(service, plan(), hedge=True, override=23010.0)
    assert result.success
    req = canonical.submit_order.await_args.args[0]
    assert req.order_type == "LIMIT"
    assert req.price == 23010
    assert req.payload["limit_price"] == 23010

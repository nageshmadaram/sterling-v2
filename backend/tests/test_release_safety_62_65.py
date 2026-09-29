"""Regression checks for release safety incidents #62–#65."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import live_safety
from app.services.execution.order_router import OrderRouter, OrderRouterRequest, RouterDeps, RouterMode
from app.services.webhook_store import _validate_webhook_url


@pytest.fixture
def router_deps(monkeypatch):
    monkeypatch.setattr(
        live_safety, "assert_safe_to_trade",
        lambda **kwargs: live_safety.SafetyDecision(True),
    )
    monkeypatch.setattr(live_safety, "record_idempotency", lambda *args: None)
    monkeypatch.setattr(live_safety, "enqueue_retry", Mock(return_value=SimpleNamespace(id="retry")))
    return RouterDeps(
        list_open_positions=lambda: [],
        create_paper_position=Mock(return_value="paper-1"),
    )


@pytest.mark.asyncio
async def test_shadow_never_places_or_closes_a_broker_order(router_deps):
    adapter = Mock()
    adapter.get_index_price = AsyncMock(return_value=100.0)
    adapter.place_order = AsyncMock()
    adapter.place_order_option = AsyncMock()
    adapter.market_reduce_close = AsyncMock()
    router = OrderRouter(RouterMode.SHADOW, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))
    response = await router.submit(OrderRouterRequest("BTC", "long", "futures"))
    assert response.accepted and response.mode == "shadow"
    assert response.order_id is None and response.paper_position_id == "paper-1"
    assert (await router.execute_partial_market_close("BTC", 1, "long"))["note"] == "simulated partial fill"
    adapter.place_order.assert_not_awaited()
    adapter.place_order_option.assert_not_awaited()
    adapter.market_reduce_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_greeks_failure_rejects_before_broker(router_deps):
    def unavailable(*args):
        raise RuntimeError("chain unavailable")
    router_deps.greeks_budget_gate = unavailable
    adapter = Mock()
    adapter.place_order = AsyncMock()
    router = OrderRouter(RouterMode.LIVE, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))
    response = await router.submit(OrderRouterRequest("BTC", "long", "futures"))
    assert not response.accepted and response.code == "greeks_budget_unknown"
    adapter.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_keeps_protective_order_fields(router_deps):
    router_deps.greeks_budget_gate = lambda *args: None
    adapter = Mock()
    adapter.get_product_id = AsyncMock(return_value=1)
    adapter.place_order = AsyncMock(side_effect=RuntimeError("broker unavailable"))
    router = OrderRouter(RouterMode.LIVE, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))
    request = OrderRouterRequest(
        "BTC", "long", "futures", stop_loss=90, take_profit=120,
        trail_amount=5, order_type="limit", limit_price=100,
        time_in_force="gtc", client_order_id="fixed",
    )
    response = await router.submit(request)
    assert not response.accepted and response.retry_id == "retry"
    payload = live_safety.enqueue_retry.call_args.kwargs["payload"]
    for field in ("stop_loss", "take_profit", "trail_amount", "order_type",
                  "limit_price", "time_in_force", "reduce_only", "option_symbol"):
        assert payload[field] == getattr(request, field)


def test_webhook_destination_rejects_metadata_and_local(monkeypatch):
    monkeypatch.setenv("STERLING_WEBHOOK_ALLOWED_HOSTS", "hooks.example.com")
    for url in ("http://hooks.example.com/x", "https://127.0.0.1/x",
                "https://hooks.example.com@127.0.0.1/x"):
        with pytest.raises(ValueError):
            _validate_webhook_url(url)
    monkeypatch.setattr("app.services.webhook_store.socket.getaddrinfo",
                        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 443))])
    with pytest.raises(ValueError):
        _validate_webhook_url("https://hooks.example.com/x")

"""Regression checks for release safety incidents #62–#65."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import live_safety
from app.services.execution.order_router import OrderRouter, OrderRouterRequest, RouterDeps, RouterMode
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, RiskApproval
from app.services.webhook_store import _validate_webhook_url


@pytest.fixture
def router_deps(monkeypatch):
    monkeypatch.setattr(
        live_safety, "assert_safe_to_trade",
        lambda **kwargs: live_safety.SafetyDecision(True),
    )
    live_safety._IDEMPOTENCY_CACHE.clear()
    live_safety._RETRY_QUEUE.clear()
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
async def test_protected_live_entry_is_refused_when_router_cannot_attach_bracket(router_deps):
    router_deps.greeks_budget_gate = lambda *args: None
    adapter = Mock()
    adapter.get_product_id = AsyncMock(return_value=1)
    adapter.place_order = AsyncMock()
    router = OrderRouter(RouterMode.LIVE, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))
    request = OrderRouterRequest(
        "BTC", "long", "futures", stop_loss=90, take_profit=120,
        trail_amount=5, order_type="limit", limit_price=100,
        time_in_force="gtc", client_order_id="fixed",
    )
    response = await router.submit(request)
    assert not response.accepted
    assert response.code == "atomic_protection_unsupported"
    adapter.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_broker_error_is_not_enqueued_for_retry(router_deps, monkeypatch):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    router_deps.greeks_budget_gate = lambda *args: None
    adapter = Mock()
    adapter._is_paper = False
    adapter.get_product_id = AsyncMock(return_value=1)
    adapter.place_order = AsyncMock(side_effect=RuntimeError("ack lost"))
    router = OrderRouter(RouterMode.LIVE, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))

    response = await router.submit(OrderRouterRequest(
        "BTC", "long", "futures", client_order_id="fixed",
    ))

    assert not response.accepted
    assert response.code == "submission_unknown"
    assert response.retry_id is None
    assert live_safety.list_retries() == []
    assert live_safety.check_idempotency("fixed") == "UNKNOWN"
    adapter.place_order.assert_awaited_once()


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



def test_kite_accounts_default_to_paper():
    """Critical #59: an omitted mode must never mean live money."""
    from app.services.exchanges.kite.models import KiteAccountCreate

    account = KiteAccountCreate(api_key="key", api_secret="secret")
    assert account.is_paper is True



@pytest.mark.asyncio
async def test_global_live_switch_blocks_real_entry_at_canonical_boundary():
    """High #65: Production Shadow cannot send through canonical execution either."""
    class LiveBroker:
        _account_id = "acct-shadow"
        _is_paper = False

        def __init__(self):
            self.calls = 0

        async def place_order(self, **kwargs):
            self.calls += 1
            return {"order_id": "MUST-NOT-HAPPEN"}

    broker = LiveBroker()
    request = ExecutionRequest(
        uid="shadow-user", account_id="acct-shadow",
        strategy_id="sterling-kite", generation_id="g1", signal_id="sig-shadow",
        exchange="NFO", symbol="NIFTY26OCT25000CE", side="BUY", quantity=75,
    )
    approval = RiskApproval(
        approval_id="shadow-proof", uid=request.uid, account_id=request.account_id,
        strategy_id=request.strategy_id, signal_id=request.signal_id,
        symbol=request.symbol, side=request.side, quantity=request.quantity,
        generation_id=request.generation_id, available_capital=500000.0,
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval,
    )

    assert result.success is False
    assert result.status == "HALTED"
    assert "Global live execution is disabled" in result.error
    assert broker.calls == 0



def test_webhook_create_and_update_validate_destination(monkeypatch):
    """Unsafe webhook URLs are rejected before persistence, not only at send time."""
    from app.schemas.webhooks import WebhookCreate, WebhookType
    from app.services import webhook_store

    webhook_store.clear()
    monkeypatch.setenv("STERLING_WEBHOOK_ALLOWED_HOSTS", "hooks.example.com")
    monkeypatch.setattr(
        "app.services.webhook_store.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    wh = webhook_store.add_webhook(WebhookCreate(
        name="safe", webhook_type=WebhookType.ZAPIER,
        url="https://hooks.example.com/ok",
    ))
    assert wh.url == "https://hooks.example.com/ok"

    with pytest.raises(ValueError):
        webhook_store.update_webhook(wh.id, url="http://hooks.example.com/nope")

    with pytest.raises(ValueError):
        webhook_store.add_webhook(WebhookCreate(
            name="bad", webhook_type=WebhookType.ZAPIER,
            url="https://127.0.0.1/internal",
        ))



@pytest.mark.asyncio
async def test_shadow_floor_does_not_block_reduce_only_exit(router_deps):
    """Global live floor blocks entries, not exposure-reducing broker actions."""
    router_deps.greeks_budget_gate = lambda *args: None
    adapter = Mock()
    adapter._is_paper = False
    adapter.get_product_id = AsyncMock(return_value=1)
    adapter.place_order = AsyncMock(return_value={"order_id": "EXIT-1"})
    router = OrderRouter(RouterMode.LIVE, adapter, router_deps, lambda _: SimpleNamespace(symbol="BTCUSD"))

    response = await router.submit(OrderRouterRequest(
        "BTC", "long", "futures", reduce_only=True, client_order_id="exit-1",
    ))

    assert response.accepted is True
    assert response.order_id == "EXIT-1"
    adapter.place_order.assert_awaited_once()



@pytest.mark.asyncio
async def test_canonical_boundary_blocks_unknown_broker_mode():
    """A client with no explicit paper/live identity is UNKNOWN, never paper."""
    class UnknownBroker:
        def __init__(self):
            self.calls = 0

        async def place_order(self, **kwargs):
            self.calls += 1
            return {"order_id": "MUST-NOT-HAPPEN"}

    broker = UnknownBroker()
    request = ExecutionRequest(
        uid="unknown-mode-user", account_id="acct-unknown",
        strategy_id="sterling-kite", generation_id="g1", signal_id="sig-unknown",
        exchange="NFO", symbol="NIFTY26OCT25000CE", side="BUY", quantity=75,
    )
    approval = RiskApproval(
        approval_id="unknown-mode-proof", uid=request.uid, account_id=request.account_id,
        strategy_id=request.strategy_id, signal_id=request.signal_id,
        symbol=request.symbol, side=request.side, quantity=request.quantity,
        generation_id=request.generation_id, available_capital=500000.0,
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval,
    )

    assert result.success is False
    assert result.status == "HALTED"
    assert "broker mode is unknown" in result.error
    assert broker.calls == 0

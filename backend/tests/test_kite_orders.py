"""Kite order/GTT payload mapping + paper-mode + contract edge cases."""
from urllib.parse import parse_qs

import httpx
import pytest

from app.services.exchanges.kite import constants as K
from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.kite.errors import KiteError, KiteOrderError


@pytest.fixture(autouse=True)
def _canonical_capability():
    """These tests exercise the transport's payload mapping on a live-mode client.

    The broker-write guard (always on for live clients) refuses any entry write
    that does not hold a canonical capability, so the transport tests hold one for
    their duration. The guard itself is covered in test_snapback_family_mode.py.
    """
    from app.services.snapback_family_mode import canonical_broker_capability

    with canonical_broker_capability("transport-payload-test"):
        yield


def _client(handler, *, is_paper=False):
    c = KiteClient(api_key="ak", api_secret="sec", access_token="tok", is_paper=is_paper)
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.kite.trade",
        headers={"X-Kite-Version": "3"},
    )
    return c


def _capture():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        body = request.content.decode() if request.content else ""
        seen["form"] = {k: v[0] for k, v in parse_qs(body).items()} if body else {}
        seen["ctype"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "ORD1"}})

    return handler, seen


async def test_place_order_market_payload():
    handler, seen = _capture()
    c = _client(handler)
    out = await c.place_order("NSE:INFY", "buy", 10, product=K.PRODUCT_CNC)
    assert out["order_id"] == "ORD1"
    assert seen["method"] == "POST"
    assert seen["path"] == "/orders/regular"
    assert "application/x-www-form-urlencoded" in seen["ctype"]
    f = seen["form"]
    assert f["exchange"] == "NSE"
    assert f["tradingsymbol"] == "INFY"
    assert f["transaction_type"] == "BUY"
    assert f["quantity"] == "10"
    assert f["order_type"] == "MARKET"
    assert f["product"] == "CNC"
    assert float(f["market_protection"]) == -1
    assert "price" not in f  # market order carries no price


async def test_place_order_limit_payload():
    handler, seen = _capture()
    c = _client(handler)
    await c.place_order("NSE:INFY", "sell", 5, order_type="limit_order", limit_price=1499.5)
    f = seen["form"]
    assert f["order_type"] == "LIMIT"
    assert f["transaction_type"] == "SELL"
    assert f["price"] == "1499.5"
    assert f["validity"] == "DAY"
    assert "market_protection" not in f


async def test_place_order_stoploss_trigger():
    handler, seen = _capture()
    c = _client(handler)
    await c.place_order("NSE:INFY", "sell", 5, kite_order_type="SL-M", trigger_price=1400)
    f = seen["form"]
    assert f["order_type"] == "SL-M"
    assert f["trigger_price"] == "1400"
    assert float(f["market_protection"]) == -1


@pytest.mark.parametrize("method", ["place_order_option", "place_order_future"])
async def test_derivatives_session_rejection_never_becomes_next_day_amo(method):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={"status":"error", "message":"Market closed",
            "data":{"hints":["switch_to_amo"]}, "error_type":"InputException"})
    c = _client(handler)
    with pytest.raises(KiteError):
        await getattr(c, method)("CONTRACT", "buy", 50)
    assert calls == ["/orders/regular"]


@pytest.mark.parametrize("value", [0, 101, float("nan")])
async def test_unprotected_market_order_rejected_before_network(value):
    handler, seen = _capture()
    c = _client(handler)
    with pytest.raises(KiteOrderError):
        await c.place_order("NSE:INFY", "buy", 1, market_protection=value)
    assert seen == {}


async def test_error_message_containing_rate_is_not_a_retry_signal():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={"status":"error", "message":"Corporate action restriction",
            "data":{}, "error_type":"InputException"})
    c = _client(handler)
    with pytest.raises(KiteError):
        await c.place_order_option("CONTRACT", "buy", 50)
    assert calls == ["/orders/regular"]


async def test_place_order_paper_returns_mock_without_network():
    async def boom(request):  # must never be called in paper mode
        raise AssertionError("network hit in paper mode")
    c = _client(boom, is_paper=True)
    out = await c.place_order("NSE:INFY", "buy", 1)
    assert out["order_id"].startswith("PAPER-")


async def test_family_mode_paper_mutations_do_not_require_live_capability(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")

    def boom(request):  # pragma: no cover - paper mutations must never hit HTTP
        raise AssertionError(f"paper mutation hit network: {request.method} {request.url}")

    c = _client(boom, is_paper=True)

    assert (await c.place_order("NSE:INFY", "buy", 1))["paper"] is True
    assert (await c.cancel_order("PAPER-1"))["order_id"] == "PAPER-1"
    assert (await c.modify_order("PAPER-1", price=100.0))["order_id"] == "PAPER-1"

    gtt = await c.place_gtt(
        trigger_type="single",
        tradingsymbol="INFY",
        exchange="NSE",
        last_price=100.0,
        trigger_values=[95.0],
        orders=[{"tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "SELL",
                 "quantity": 10, "product": "CNC", "order_type": "LIMIT", "price": 1445.65}],
    )
    assert int(gtt["trigger_id"]) > 0
    assert (await c.modify_gtt(
        gtt["trigger_id"],
        trigger_type="single",
        tradingsymbol="INFY",
        exchange="NSE",
        last_price=100.0,
        trigger_values=[96.0],
        orders=[{"tradingsymbol": "INFY", "transaction_type": "SELL"}],
    ))["paper"] is True
    assert (await c.delete_gtt(gtt["trigger_id"]))["trigger_id"] == gtt["trigger_id"]

    mf = await c.place_mf_order(
        tradingsymbol="INFY", transaction_type="BUY", amount=1000
    )
    assert mf["order_id"].startswith("PAPER-MF-")
    assert (await c.cancel_mf_order(mf["order_id"]))["order_id"] == mf["order_id"]

    sip = await c.place_mf_sip(
        tradingsymbol="INFY", amount=1000, instalments=12, frequency="monthly"
    )
    assert sip["sip_id"].startswith("PAPER-SIP-")
    assert (await c.modify_mf_sip(sip["sip_id"], amount=1200))["sip_id"] == sip["sip_id"]
    assert (await c.cancel_mf_sip(sip["sip_id"]))["sip_id"] == sip["sip_id"]
    assert (await c.convert_position(exchange="NSE", tradingsymbol="INFY"))["status"] == "paper"


@pytest.fixture()
def _no_capability():
    """Drop the capability the autouse fixture grants, to test the refusal itself."""
    from app.services import snapback_family_mode as sfm

    token = sfm._capability.set(None)
    try:
        yield
    finally:
        sfm._capability.reset(token)


@pytest.mark.parametrize("family_mode", [None, "true"])
async def test_live_transport_entry_writes_require_capability(
    family_mode, monkeypatch, _no_capability
):
    """A direct entry write on a live client is refused before the network, with or
    without the retired Family Mode variable."""
    if family_mode is None:
        monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)
    else:
        monkeypatch.setenv("STERLING_FAMILY_MODE", family_mode)

    def boom(request):  # pragma: no cover - guard must refuse before HTTP
        raise AssertionError(f"unguarded live mutation hit network: {request.method} {request.url}")

    c = _client(boom, is_paper=False)
    with pytest.raises(PermissionError):
        await c.place_order("NSE:INFY", "buy", 1)
    with pytest.raises(PermissionError):
        await c.convert_position(exchange="NSE", tradingsymbol="INFY")
    if family_mode:
        # The retired switch only tightens: GTT removal needs a capability too.
        with pytest.raises(PermissionError):
            await c.delete_gtt(123)


async def test_post_only_rejected():
    handler, _ = _capture()
    c = _client(handler)
    with pytest.raises(KiteOrderError):
        await c.place_order("NSE:INFY", "buy", 1, post_only=True)


async def test_cancel_order_uses_variety_ignores_product_id():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "ORD1"}})

    c = _client(handler)
    await c.cancel_order("ORD1", 999, variety="amo")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/orders/amo/ORD1"


async def test_place_gtt_encodes_condition_and_orders_as_json():
    handler, seen = _capture()
    c = _client(handler)
    await c.place_gtt(
        trigger_type="single", tradingsymbol="INFY", exchange="NSE",
        last_price=1500, trigger_values=[1450],
        orders=[{"tradingsymbol": "INFY", "exchange": "NSE", "transaction_type": "SELL",
                 "quantity": 10, "product": "CNC", "order_type": "LIMIT", "price": 1445.65}],
    )
    f = seen["form"]
    assert seen["path"] == "/gtt/triggers"
    assert f["type"] == "single"
    import json
    cond = json.loads(f["condition"])
    assert cond["trigger_values"] == [1450]
    assert cond["tradingsymbol"] == "INFY"
    orders = json.loads(f["orders"])
    assert orders[0]["transaction_type"] == "SELL"
    assert orders[0]["price"] == 1445.65


@pytest.mark.parametrize("field,value", [
    ("product", "MIS"), ("order_type", "MARKET"), ("price", 0),
    ("price", "invalid"), ("price", float("nan")), ("quantity", True),
    ("quantity", -1), ("exchange", "NFO"), ("tradingsymbol", "OTHER"),
])
async def test_invalid_gtt_never_reaches_broker(field, value):
    handler, seen = _capture()
    c = _client(handler)
    order = {"exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "SELL",
             "quantity": 10, "product": "CNC", "order_type": "LIMIT", "price": 1445.65}
    order[field] = value
    with pytest.raises(KiteOrderError):
        await c.place_gtt(trigger_type="single", tradingsymbol="INFY", exchange="NSE",
                          last_price=1500, trigger_values=[1450], orders=[order])
    assert not seen
    await c._client.aclose()


def test_short_oco_prices_match_triggers_and_preserve_reference_quote(monkeypatch):
    from app.services.kite_engine.protective_stop import GTT_LIMIT_BUFFER_ENV, build_gtt_request
    monkeypatch.setenv(GTT_LIMIT_BUFFER_ENV, "0.3")
    request = build_gtt_request(tradingsymbol="CONTRACT", exchange="NFO", qty=50,
                                trigger_premium=120, target_premium=80,
                                last_price=100, direction="short", product="NRML")
    body = KiteClient._gtt_body(**request)
    import json
    condition = json.loads(body["condition"])
    orders = json.loads(body["orders"])
    assert condition["last_price"] == 100
    assert condition["trigger_values"] == [80, 120]
    assert [order["price"] for order in orders] == [80.25, 120.4]
    assert all(order["transaction_type"] == "BUY" and order["order_type"] == "LIMIT" for order in orders)


async def test_regular_order_auto_retries_as_amo_when_market_closed():
    """Markets closed: Zerodha 400s a regular order with hint `switch_to_amo`.
    Mirror the Kite web app — auto-resubmit to /orders/amo and flag the result."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/orders/regular":
            return httpx.Response(400, json={
                "status": "error",
                "message": "Your order could not be converted to a After Market Order (AMO).",
                "data": {"hints": ["switch_to_amo"]},
                "error_type": "InputException",
            })
        return httpx.Response(200, json={"status": "success", "data": {"order_id": "AMO99"}})

    c = _client(handler)
    out = await c.place_order("NSE:INFY", "buy", 10, product=K.PRODUCT_CNC)
    assert calls == ["/orders/regular", "/orders/amo"]
    assert out["order_id"] == "AMO99"
    assert out["amo"] is True


async def test_regular_order_error_without_amo_hint_does_not_retry():
    """A plain rejection (no switch_to_amo hint) must NOT silently become an AMO."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={
            "status": "error", "message": "Insufficient funds",
            "data": {}, "error_type": "MarginException",
        })

    c = _client(handler)
    with pytest.raises(KiteError):
        await c.place_order("NSE:INFY", "buy", 10)
    assert calls == ["/orders/regular"]


async def test_order_margins_posts_json_array():
    seen = {}

    def handler(request):
        seen["ctype"] = request.headers.get("content-type", "")
        import json
        seen["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"status": "success", "data": [{"total": 123.0}]})

    c = _client(handler)
    res = await c.order_margins([{"exchange": "NSE", "tradingsymbol": "INFY"}])
    assert "application/json" in seen["ctype"]
    assert seen["json"][0]["tradingsymbol"] == "INFY"
    assert res[0]["total"] == 123.0



@pytest.mark.asyncio
async def test_modify_gtt_paper_never_hits_network():
    """Paper mode may model protection but must never mutate a real Kite GTT."""
    async def boom(request):
        raise AssertionError(f"network hit in paper mode: {request.url}")

    client = KiteClient(api_key="ak", api_secret="sec", access_token="tok", is_paper=True)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    try:
        result = await client.modify_gtt(
            123,
            trigger_type="single",
            tradingsymbol="NIFTY26SEP25000CE",
            exchange="NFO",
            last_price=100.0,
            trigger_values=[80.0],
            orders=[{
                "exchange": "NFO",
                "tradingsymbol": "NIFTY26SEP25000CE",
                "transaction_type": "SELL",
                "quantity": 75,
                "order_type": "LIMIT",
                "product": "NRML",
                "price": 79.0,
            }],
        )
        assert result == {"trigger_id": 123, "paper": True}
    finally:
        await client.close()

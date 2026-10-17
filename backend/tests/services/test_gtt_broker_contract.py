"""Protection exercises the actual Kite HTTP transport without broker writes."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.services.exchanges.kite.client import KiteClient
from app.services.kite_engine.protective_stop import move_stop, place_stop
from app.services.snapback_family_mode import INTENT_PROTECT, canonical_broker_capability

pytestmark = pytest.mark.usefixtures("allow_live_execution")


@pytest.mark.parametrize("settled,used,day_net,expected", [(100,0,0,321),(0,0,0,None),
    (100,80,0,None),(100,0,-80,None)])
async def test_overnight_cnc_protection_uses_settled_holdings_without_double_counting(settled,used,day_net,expected):
    calls=[]
    def handle(request):
        calls.append((request.method,request.url.path,request.content))
        if request.url.path=="/portfolio/positions":
            data={"net":[{"exchange":"NSE","tradingsymbol":"INFY","product":"CNC","quantity":day_net}]}
        elif request.url.path=="/portfolio/holdings":
            data=[{"exchange":"NSE","tradingsymbol":"INFY","product":"CNC","quantity":settled,
                   "realised_quantity":settled,"used_quantity":used,"collateral_quantity":0,
                   "t1_quantity":500,"discrepancy":False}]
        elif request.url.path=="/quote/ltp":
            data={"NSE:INFY":{"last_price":100}}
        elif request.url.path=="/gtt/triggers":
            data={"trigger_id":321}
        else:
            raise AssertionError(request.url.path)
        return httpx.Response(200,json={"status":"success","data":data})
    client=KiteClient(api_key="test",api_secret="test",access_token="test",is_paper=False)
    client._client=httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url="https://api.kite.trade")
    try:
        with canonical_broker_capability("test-settled-holdings",intent=INTENT_PROTECT):
            result=await place_stop(client,tradingsymbol="INFY",exchange="NSE",qty=50,
                                    trigger_premium=90,last_price=100,product="CNC")
        assert result==expected
        assert (any(method=="POST" for method,_,_ in calls)) == (expected is not None)
    finally:
        await client._client.aclose()


def client_and_calls(*, quote=123.4, product="NRML", quantity=50):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path, request.content))
        if request.url.path == "/portfolio/positions":
            data = {"net": [{"tradingsymbol": "TESTCE", "exchange": "NFO",
                              "quantity": quantity, "product": product}]}
        elif request.url.path == "/quote/ltp":
            data = {"NFO:TESTCE": {"last_price": quote}}
        else:
            data = {"trigger_id": 321}
        return httpx.Response(200, json={"status": "success", "data": data})

    client = KiteClient(api_key="test", api_secret="test", access_token="test", is_paper=False)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="https://api.kite.trade")
    return client, calls


async def test_protection_uses_fresh_quote_and_tick_sized_limit_leg(monkeypatch):
    from app.services.kite_engine.protective_stop import GTT_LIMIT_BUFFER_ENV
    monkeypatch.setenv(GTT_LIMIT_BUFFER_ENV, "0.3")
    client, calls = client_and_calls()
    with canonical_broker_capability("test-fresh-quote", intent=INTENT_PROTECT):
        result = await place_stop(client, tradingsymbol="TESTCE", exchange="NFO", qty=50,
                                  trigger_premium=115.3, last_price=100, product="NRML", tick_size=0.1)
    await client._client.aclose()
    assert result == 321
    assert [(method, path) for method, path, _ in calls] == [
        ("GET", "/portfolio/positions"), ("GET", "/quote/ltp"), ("POST", "/gtt/triggers"),
    ]
    form = parse_qs(calls[-1][2].decode())
    assert json.loads(form["condition"][0])["last_price"] == 123.4
    leg = json.loads(form["orders"][0])[0]
    assert (leg["order_type"], leg["product"], leg["quantity"], leg["price"]) == ("LIMIT", "NRML", 50, 114.9)


@pytest.mark.parametrize("quote", [0, None, "invalid"])
async def test_bad_quote_never_uses_entry_price_as_live_protection(quote):
    client, calls = client_and_calls(quote=quote)
    with canonical_broker_capability("test-missing-quote", intent=INTENT_PROTECT):
        result = await place_stop(client, tradingsymbol="TESTCE", exchange="NFO", qty=50,
                                  trigger_premium=80, last_price=100, product="NRML")
    await client._client.aclose()
    assert result is None
    assert all(method == "GET" for method, _, _ in calls)


async def test_mis_inventory_is_unprotected_despite_valid_quote():
    client, calls = client_and_calls(product="MIS")
    with canonical_broker_capability("test-unsupported-product", intent=INTENT_PROTECT):
        result = await place_stop(client, tradingsymbol="TESTCE", exchange="NFO", qty=50,
                                  trigger_premium=80, last_price=100, product="MIS")
    await client._client.aclose()
    assert result is None
    assert [path for _, path, _ in calls] == ["/portfolio/positions"]


@pytest.mark.parametrize("quote", [79, 80])
async def test_breached_stop_never_becomes_an_upward_sell_trigger(quote):
    client, calls = client_and_calls(quote=quote)
    submitted = []
    with canonical_broker_capability("test-breached-stop", intent=INTENT_PROTECT):
        result = await place_stop(client, tradingsymbol="TESTCE", exchange="NFO", qty=50,
                                  trigger_premium=80, last_price=100, product="NRML",
                                  before_submit=lambda: submitted.append(True))
    await client._client.aclose()
    assert result is None and not submitted
    assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("action", ["place", "trail"])
@pytest.mark.parametrize("direction,quote,stop,target", [
    ("long", 120, 80, 120), ("long", 121, 80, 120),
    ("short", 80, 120, 80), ("short", 79, 120, 80),
])
async def test_crossed_target_requires_exit_reconciliation(action, direction, quote, stop, target):
    client, calls = client_and_calls(quote=quote, quantity=50 if direction == "long" else -50)
    submitted = []
    params = dict(tradingsymbol="TESTCE", exchange="NFO", qty=50,
                  trigger_premium=stop, target_premium=target, last_price=100,
                  product="NRML", direction=direction)
    with canonical_broker_capability("test-crossed-target", intent=INTENT_PROTECT):
        if action == "place":
            result = await place_stop(client, **params, before_submit=lambda: submitted.append(True))
            assert result is None and not submitted
        else:
            assert await move_stop(client, trigger_id=321, **params) is False
    await client._client.aclose()
    assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("action", ["place", "trail"])
@pytest.mark.parametrize("direction,stop", [("long", 99.98), ("short", 100.02)])
async def test_tick_rounding_cannot_move_a_stop_across_the_market(action, direction, stop):
    client, calls = client_and_calls(quote=100, quantity=50 if direction == "long" else -50)
    params = dict(tradingsymbol="TESTCE", exchange="NFO", qty=50,
                  trigger_premium=stop, last_price=100, tick_size=0.1,
                  product="NRML", direction=direction)
    with canonical_broker_capability("test-rounded-stop", intent=INTENT_PROTECT):
        if action == "place":
            assert await place_stop(client, **params) is None
        else:
            assert await move_stop(client, trigger_id=321, **params) is False
    await client._client.aclose()
    assert all(method == "GET" for method, _, _ in calls)

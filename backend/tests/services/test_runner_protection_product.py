"""Legacy MIS exposure cannot borrow another product's GTT protection.

Exercise each runner through the real protection helper. Mocking place_stop or
testing only a single broker bucket misses the dangerous case where an omitted
product can arm an NRML exit for a position managed as MIS.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engines.adaptive_edge import AdaptiveEdgeConfig
from app.engines.adaptive_edge.state_machine import StrategyState
from app.engines.gamma_move import GammaMoveConfig, InstrumentRef, PositionState
from app.engines.intraday import IntradayConfig
from app.engines.intraday.position import ContractRef, IntradayPosition
from app.services import adaptive_edge_positions, adaptive_edge_runner
from app.services import gamma_move_positions, gamma_move_runner
from app.services import intraday_positions, intraday_runner


UID = "protection-product-test"
SYMBOL = "NIFTY26OCT25000PE"


class Broker:
    _account_id = "product-test-account"

    def __init__(self, rows, mode=False):
        self._is_paper = mode
        self.rows = rows
        self.reads = 0
        self.writes = []

    async def get_positions_raw(self):
        self.reads += 1
        return {"net": self.rows}

    async def place_gtt(self, **request):
        self.writes.append(("place", request))
        return {"trigger_id": 123}

    async def modify_gtt(self, trigger_id, **request):
        self.writes.append(("modify", request))
        return {"trigger_id": trigger_id}


def row(product="MIS", quantity=50, exchange="NFO"):
    return {
        "tradingsymbol": SYMBOL,
        "exchange": exchange,
        "quantity": quantity,
        "product": product,
    }


@pytest.fixture(params=["intraday", "adaptive_edge", "gamma_move"])
def runner_case(request, monkeypatch):
    # Persist through each runner's actual store, while keeping test state local.
    data = {}
    from app.services import db

    monkeypatch.setattr(db, "get_config", lambda key, default="": data.get(key, default))
    monkeypatch.setattr(db, "set_config", lambda key, value: data.__setitem__(key, value))
    monkeypatch.setattr(db, "get_config_strict", lambda key, default="": data.get(key, default))
    monkeypatch.setattr(db, "set_config_strict", lambda key, value: data.__setitem__(key, value))

    if request.param == "intraday":
        runner, store, cfg = intraday_runner, intraday_positions, IntradayConfig()
        position = IntradayPosition(
            strategy="pivot_break", signal_id="product-test", underlying="NIFTY",
            contract=ContractRef(SYMBOL, exchange="NFO", option_type="PE"),
            thesis="BEARISH", entry=100.0, stop=70.0, target=150.0,
            quantity=50, lots=1, status="open", gtt_id=123,
        )
    elif request.param == "adaptive_edge":
        runner, store, cfg = (
            adaptive_edge_runner, adaptive_edge_positions, AdaptiveEdgeConfig()
        )
        position = adaptive_edge_positions.AdaptiveEdgePosition(
            symbol=SYMBOL, token=1, underlying="NIFTY", direction="PE",
            quantity=50, lot_size=50, entry_price=100.0, stop_price=70.0,
            target_price=150.0, state=StrategyState.OPEN.value, gtt_id=123,
        )
    else:
        runner, store, cfg = gamma_move_runner, gamma_move_positions, GammaMoveConfig()
        position = PositionState(
            signal_id="product-test", list_name="bullish", tier="ENTRY",
            instrument=InstrumentRef(
                instrument_id="1", tradingsymbol=SYMBOL, option_type="PE",
                strike=25000.0, expiry="2026-10-29", exchange="NFO",
            ),
            underlying="NIFTY", underlying_token=100, hold="intraday",
            zone_edge=24800.0, base_low=70.0, t1=120.0, t2=150.0,
            entry=100.0, stop=70.0, quantity=50, lots=1, stop_mode="both", product="MIS",
            entered_ms=1, entry_day="2026-09-30", status="open", gtt_id=123,
        )

    position.account_id = Broker._account_id
    store.reset(UID)
    store.put(UID, position)
    store.reset(UID)
    position = store.get(UID, SYMBOL)
    assert position is not None

    async def protect(client, action):
        async def current_client(uid):
            return client

        monkeypatch.setattr(runner, "_client", current_client)
        if request.param == "gamma_move":
            position.gtt_id = 0 if action == "place" else 123
            return await runner._ensure_protection(UID, client, position)
        if action == "place":
            return await runner._place_protection(UID, client, position, cfg)
        if request.param == "intraday":
            return await runner._sync_protection(UID, position, cfg)
        if request.param == "adaptive_edge":
            return await runner._sync_trail(UID, client, position, cfg)
        return await runner._sync_trail(UID, position, cfg)

    yield SimpleNamespace(protect=protect, position=position, stop_orders=1 if request.param == "gamma_move" else 2)
    store.reset(UID)


@pytest.mark.parametrize("action", ["place", "modify"])
async def test_legacy_mis_is_not_falsely_reported_as_gtt_protected(runner_case, action):
    client = Broker([row("NRML", 100), row("MIS", 50)])

    await runner_case.protect(client, action)

    assert client.reads == 1
    assert client.writes == []
    if hasattr(runner_case.position, "product"):
        assert runner_case.position.product == "MIS"


@pytest.mark.parametrize("action", ["place", "modify"])
@pytest.mark.parametrize(
    "rows",
    [
        [row("NRML", 100)],
        [row("MIS", 25), row("NRML", 100)],
        [row("MIS", -50), row("NRML", 100)],
        [row("MIS", 50, "BFO"), row("NRML", 100)],
        [row("", 100)],
    ],
    ids=["nrml-only", "insufficient-mis", "short-mis", "wrong-exchange", "unknown-product"],
)
async def test_protection_refuses_inventory_outside_its_mis_bucket(runner_case, action, rows):
    client = Broker(rows)

    await runner_case.protect(client, action)

    assert client.reads == 1
    assert client.writes == []


@pytest.mark.parametrize("action", ["place", "modify"])
async def test_protection_refuses_unknown_broker_mode(runner_case, action):
    client = Broker([row()], mode=None)

    await runner_case.protect(client, action)

    assert client.reads == 0
    assert client.writes == []


@pytest.mark.parametrize("action", ["place", "modify"])
async def test_paper_protection_does_not_fall_back_to_nrml(runner_case, action):
    client = Broker([], mode=True)

    await runner_case.protect(client, action)

    assert client.reads == 0
    assert len(client.writes) == 1
    assert {order["product"] for order in client.writes[0][1]["orders"]} == {"MIS"}

"""An auto-entry must not open a contract the engine will square off at once.

``expiry_square_off_days`` (default 1) market-exits an option position as soon as its
contract is within that many calendar days of expiry. The entry path chose the leg
nearest the money with no regard to that window: on the eve of a weekly expiry the
scanner lists the weekly first, the entry bought it, and the very next maintenance
pass sold it again, paying the spread and the charges for a round trip nobody wanted.

The entry now skips legs that are already inside the window and trades the nearest
one outside it; when every candidate is inside, it refuses and says why.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.kite_engine import positions as pos
from app.services.kite_engine import service, state
from tests.engines.sterling_kite_engine.canonical_double import CanonicalBrokerDouble

pytestmark = pytest.mark.usefixtures("allow_live_execution", "execution_policy_open_session")

_IST = timezone(timedelta(hours=5, minutes=30))
UID = "expiry-window"


@pytest.fixture(autouse=True)
def _valid_entry_data_for_execution_plumbing(monkeypatch):
    monkeypatch.setattr(service, "entry_data_block_reason", lambda *a, **kw: "")


class _Client(CanonicalBrokerDouble):
    _is_paper = True

    def __init__(self):
        self.placed: list = []

    async def get_margins(self, segment="equity"):
        return {"available": {"live_balance": 5_000_000}}

    async def get_ltp(self, keys):
        return {k: {"last_price": 90.0} for k in keys}

    async def place_order_option(self, sym, side, size, **kw):
        self.placed.append(sym)
        return {"order_id": "O-" + sym}

    async def place_gtt(self, **kw):
        return {"trigger_id": 4242}


def _day(days_ahead: int) -> str:
    return (datetime.now(_IST).date() + timedelta(days=days_ahead)).isoformat()


def _row(*legs):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg

    return EngineSignalRow(
        underlying="NIFTY", token=111, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol=symbol, strike=25000,
                        expiry=expiry, lot_size=75) for symbol, expiry in legs],
        spot=25000.0, underlying_spot=25000.0, stop_loss=24950.0, score=85.0,
        timestamp_ms=1000, source="spot")


def _log():
    return [f"{e.kind}: {e.message}" for e in state.activity(UID)]


def _fresh(**config):
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel

    state.reset(UID)
    pos.reset(UID)
    cfg = EngineConfigModel(**{"risk_sizing": False, **config}).model_dump()
    state.set_config(UID, EngineConfigModel(**cfg))
    return _Client()


@pytest.mark.asyncio
async def test_a_leg_inside_the_square_off_window_is_skipped_for_the_next_expiry():
    client = _fresh()
    row = _row(("NIFTYWEEKLY25000CE", _day(1)), ("NIFTYMONTHLY25000CE", _day(15)))

    await service._make_place_cb(client, UID)(row, None)

    assert client.placed == ["NIFTYMONTHLY25000CE"], (
        "the entry bought a contract that the T-1 expiry square-off sells at the next pass", _log())


@pytest.mark.asyncio
async def test_every_leg_inside_the_window_refuses_and_says_why():
    client = _fresh()
    row = _row(("NIFTYWEEKLY25000CE", _day(1)), ("NIFTYTODAY25000CE", _day(0)))

    await service._make_place_cb(client, UID)(row, None)

    assert client.placed == [], _log()
    blocked = [e.message for e in state.activity(UID) if e.kind == "order_blocked"]
    assert blocked and "expiry square-off" in blocked[-1], blocked


@pytest.mark.asyncio
async def test_with_the_square_off_disabled_the_nearest_expiry_is_still_traded():
    client = _fresh(expiry_square_off_days=0)
    row = _row(("NIFTYWEEKLY25000CE", _day(1)), ("NIFTYMONTHLY25000CE", _day(15)))

    await service._make_place_cb(client, UID)(row, None)

    assert client.placed == ["NIFTYWEEKLY25000CE"], _log()


@pytest.mark.asyncio
async def test_a_leg_outside_the_window_is_unaffected():
    client = _fresh()
    row = _row(("NIFTYMONTHLY25000CE", _day(15)))

    await service._make_place_cb(client, UID)(row, None)

    assert client.placed == ["NIFTYMONTHLY25000CE"], _log()

"""Supertrend exits against a fired GTT whose LIMIT child rests, and the other
exit/protection defects of the same family (second adversarial review, Q2).

A broker GTT submits a priced LIMIT child when it fires. In a gap the child rests
OPEN and the GTT is consumed, so nothing at the broker will sell the position while
the premium keeps falling. The exit path must cancel that child (INTENT_CANCEL),
book whatever it filled, and sell only what the broker still holds.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("allow_live_execution")

import app.services.exchanges.kite.ticker_manager as _ticker_manager
from app.services.exchanges.kite.errors import KiteOrderError
from app.services.kite_engine import monitor
from app.services.kite_engine import positions as pos
from app.services.kite_engine import protective_stop as ps
from tests.engines.sterling_kite_engine.test_protection import _FakeClient, _held

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NIFTY24JUN24000CE"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def _noop(uid, tokens, **kw):
        return {"ok": True}

    monkeypatch.setattr(_ticker_manager, "unsubscribe", _noop)
    monkeypatch.setattr(_ticker_manager, "subscribe", _noop)
    monkeypatch.setattr(ps, "CHILD_CANCEL_POLL_S", 0.0)
    monitor.forget_holdings()
    monitor._stop_probe.clear()
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)


def _stamp(seconds_ago: float) -> str:
    return (datetime.now(IST) - timedelta(seconds=seconds_ago)).replace(
        tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


class _FiredChildClient(_FakeClient):
    """A live broker whose protective GTT fired and whose LIMIT child is still OPEN.

    ``after_cancel`` is the child's final state once the cancel lands: how much it
    filled, and whether the cancel is visible at all. The broker's holding follows it.
    """

    _is_paper = False

    def __init__(self, *, child_age_s: float = 300.0, held: int = 50, child_price: float = 78.4,
                 filled_at_cancel: int = 0, final_status: str = "CANCELLED",
                 cancel_lands: bool = True, rest_of_book=None, **kw):
        super().__init__(cancel_error="Trigger is already triggered", gtt_status="triggered", **kw)
        self.cancelled_orders: list = []
        self.child = {
            "order_id": "CHILD-1", "tradingsymbol": SYMBOL, "exchange": "NFO",
            "product": "NRML", "transaction_type": "SELL", "status": "OPEN",
            "quantity": 50, "filled_quantity": 0, "average_price": 0.0,
            "price": child_price, "order_timestamp": _stamp(child_age_s),
        }
        self.held = held
        self.filled_at_cancel = filled_at_cancel
        self.final_status = final_status
        self.cancel_lands = cancel_lands
        self.rest_of_book = list(rest_of_book or [])

    async def get_orders(self):
        self.calls.append("get_orders")
        return [dict(self.child)] + list(self.rest_of_book)

    async def get_order_history(self, order_id):
        self.calls.append("order_history")
        return [dict(self.child)] if order_id == "CHILD-1" else []

    async def cancel_order(self, order_id, *a, **kw):
        from app.services.snapback_family_mode import INTENT_CANCEL, current_intent

        assert current_intent() == INTENT_CANCEL, "child cancelled without CANCEL authority"
        self.calls.append("cancel_order")
        self.cancelled_orders.append(order_id)
        if self.cancel_lands:
            self.child["status"] = self.final_status
            if self.filled_at_cancel:
                self.child["filled_quantity"] = self.filled_at_cancel
                self.child["average_price"] = 78.4
                self.held -= self.filled_at_cancel
        return {"order_id": order_id}

    async def get_positions_raw(self):
        self.calls.append("positions")
        return {"net": [{"tradingsymbol": SYMBOL, "exchange": "NFO", "product": "NRML",
                         "quantity": self.held}]}


def _position(uid: str, **kw):
    p = _held(uid, stop=80.0, **kw)
    p.product = "NRML"
    return p


# ── findings 3 + 9: a fired stop whose child rests unfilled ───────────────────

@pytest.mark.asyncio
async def test_stale_child_is_cancelled_then_the_position_is_sold():
    p = _position("fc-stale")
    client = _FiredChildClient()

    sold = await monitor._exit_position(client, "fc-stale", p, 60.0, price_stop_exit=True)

    assert sold is True
    assert client.cancelled_orders == ["CHILD-1"]
    assert client.sells == [(SYMBOL, "sell", 50)]
    assert client.calls.index("cancel_order") < client.calls.index("sell"), (
        "the child must be dead BEFORE the market SELL goes out")
    held = pos.get("fc-stale", SYMBOL)
    assert held.gtt_id == 0
    assert held.exit_order_id == "EXIT-1"


@pytest.mark.asyncio
async def test_part_filled_stale_child_is_booked_and_only_the_rest_is_sold():
    p = _position("fc-part")
    client = _FiredChildClient(held=50, filled_at_cancel=20)

    sold = await monitor._exit_position(client, "fc-part", p, 60.0, price_stop_exit=True)

    assert sold is True
    assert client.cancelled_orders == ["CHILD-1"]
    assert client.sells == [(SYMBOL, "sell", 30)], "20 already sold by the stop's child"
    held = pos.get("fc-part", SYMBOL)
    assert held.qty == 30
    assert held.exit_fills.get("CHILD-1") == 20, "the child's fill is booked once, by id"


@pytest.mark.asyncio
async def test_a_child_inside_the_grace_window_is_left_to_fill():
    p = _position("fc-grace")
    client = _FiredChildClient(child_age_s=3.0)

    sold = await monitor._exit_position(client, "fc-grace", p, 79.0, price_stop_exit=True)

    assert sold is False
    assert client.cancelled_orders == [] and client.sells == []


@pytest.mark.asyncio
async def test_forced_exit_does_not_wait_out_the_grace_window():
    p = _position("fc-forced")
    client = _FiredChildClient(child_age_s=3.0)

    sold = await monitor._exit_position(client, "fc-forced", p, 79.0, forced=True,
                                        reason="emergency_square_off")

    assert sold is True
    assert client.cancelled_orders == ["CHILD-1"]
    assert client.sells == [(SYMBOL, "sell", 50)]


@pytest.mark.asyncio
async def test_a_child_whose_cancel_cannot_be_confirmed_blocks_the_sell():
    p = _position("fc-unconf")
    client = _FiredChildClient(cancel_lands=False)

    sold = await monitor._exit_position(client, "fc-unconf", p, 60.0, price_stop_exit=True)

    assert sold is False
    assert client.sells == [], "a child that may still fill must never be sold over"
    assert pos.get("fc-unconf", SYMBOL).exit_order_id == ""


@pytest.mark.asyncio
async def test_a_child_that_fills_before_the_cancel_lands_is_the_exit():
    p = _position("fc-race")
    client = _FiredChildClient(final_status="COMPLETE", filled_at_cancel=50, held=50)

    sold = await monitor._exit_position(client, "fc-race", p, 60.0, price_stop_exit=True)

    assert sold is False
    assert client.sells == [], "the stop's own SELL completed; never a second one"


@pytest.mark.asyncio
async def test_emergency_square_off_flattens_a_position_whose_stop_fired_and_gapped():
    from app.services.kite_engine import service as ksvc

    p = _position("fc-emerg")
    client = _FiredChildClient(child_age_s=2.0)

    result = await ksvc.emergency_square_off_all(client, "fc-emerg")

    assert client.cancelled_orders == ["CHILD-1"]
    assert client.sells == [(SYMBOL, "sell", 50)]
    assert result["failed"] == 0
    assert result["status"] == "PENDING_BROKER_CONFIRMATION"

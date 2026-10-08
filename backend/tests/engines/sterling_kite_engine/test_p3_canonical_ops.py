"""Supertrend canonical operations: resting exits, flatten-all scope, preflight flags.

Each test pins one reproduced production defect (see the audit numbers in the
docstrings). They use the broker doubles of the neighbouring Supertrend tests.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.usefixtures("allow_live_execution")

import app.services.exchanges.kite.ticker_manager as _ticker_manager
from app.services.kite_engine import monitor
from app.services.kite_engine import positions as pos
from tests.engines.sterling_kite_engine.test_protection import _FakeClient, _held


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def _noop(uid, tokens, **kw):
        return {"ok": True}

    monkeypatch.setattr(_ticker_manager, "unsubscribe", _noop)
    monkeypatch.setattr(_ticker_manager, "subscribe", _noop)
    monitor.forget_holdings()
    monitor._stop_probe.clear()
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)


class _RestingExitClient(_FakeClient):
    """An exit SELL that rests OPEN until the engine cancels it."""

    def __init__(self, *, filled: int = 0, **kw):
        super().__init__(**kw)
        self.cancelled_orders: list = []
        self.filled = filled
        self.order_state = "OPEN"

    async def cancel_order(self, order_id, *a, **kw):
        from app.services.snapback_family_mode import INTENT_CANCEL, current_intent

        assert current_intent() == INTENT_CANCEL, "cancel sent without CANCEL authority"
        self.cancelled_orders.append(order_id)
        self.order_state = "CANCELLED"
        return {"order_id": order_id}

    async def get_order_history(self, order_id):
        self.calls.append("order_history")
        return [{
            "order_id": order_id, "status": self.order_state,
            "tradingsymbol": "NIFTY24JUN24000CE", "exchange": "NFO",
            "transaction_type": "SELL", "quantity": 50,
            "filled_quantity": self.filled, "average_price": 90.0 if self.filled else 0,
        }]


def _resting_exit(uid: str, *, age_s: float, qty: int = 50):
    p = _held(uid, gtt_id=0, qty=qty)
    p.product = "NRML"
    p.exit_order_id = "EXIT-1"
    p.exit_tag = "kxresting"
    p.exit_requested_qty = qty
    p.exit_requested_ms = int((time.time() - age_s) * 1000)
    p.exit_reason = "trail breach"
    return p


@pytest.mark.asyncio
async def test_resting_exit_is_cancelled_and_protection_rearmed():
    """Audit #3: an exit SELL that rests unfilled locked out every re-exit forever."""
    from app.services.kite_engine import service as ksvc

    p = _resting_exit("p3-rest", age_s=30)
    client = _RestingExitClient()

    await ksvc._reconcile_pending_positions(client, "p3-rest")

    held = pos.get("p3-rest", p.symbol)
    assert client.cancelled_orders == ["EXIT-1"]
    assert held.status == pos.OPEN and held.qty == 50
    assert held.exit_order_id == "" and held.exit_tag == "" and held.exit_requested_ms == 0
    assert client.gtts, "the stop trigger must be re-armed once the exit is dead"
    assert held.gtt_id == 4242


@pytest.mark.asyncio
async def test_a_young_resting_exit_is_left_alone():
    from app.services.kite_engine import service as ksvc

    p = _resting_exit("p3-young", age_s=1)
    client = _RestingExitClient()

    await ksvc._reconcile_pending_positions(client, "p3-young")

    assert client.cancelled_orders == []
    assert pos.get("p3-young", p.symbol).exit_order_id == "EXIT-1"


@pytest.mark.asyncio
async def test_partially_filled_resting_exit_is_cancelled_and_residual_protected():
    from app.services.kite_engine import service as ksvc

    p = _resting_exit("p3-part", age_s=30)
    client = _RestingExitClient(filled=20)

    await ksvc._reconcile_pending_positions(client, "p3-part")

    held = pos.get("p3-part", p.symbol)
    assert client.cancelled_orders == ["EXIT-1"]
    assert held.qty == 30, "the 20 filled lots are gone, the residual remains"
    assert held.exit_order_id == ""
    assert client.gtts and client.gtts[-1]["orders"][0]["quantity"] == 30, client.gtts


@pytest.mark.asyncio
async def test_tick_monitor_resolves_a_resting_exit_without_waiting_for_a_scan():
    p = _resting_exit("p3-tick", age_s=30)
    client = _RestingExitClient()

    await monitor.on_tick("p3-tick", p.token, 85.0, client=client)

    assert client.cancelled_orders == ["EXIT-1"]
    assert pos.get("p3-tick", p.symbol).exit_order_id == ""


@pytest.mark.asyncio
async def test_after_hours_order_is_never_cancelled():
    from app.services.kite_engine import service as ksvc

    p = _resting_exit("p3-amo", age_s=3600)
    client = _RestingExitClient()
    client.order_state = "AMO REQ RECEIVED"

    await ksvc._reconcile_pending_positions(client, "p3-amo")

    assert client.cancelled_orders == []
    assert pos.get("p3-amo", p.symbol).exit_order_id == "EXIT-1"


# ── #4: an unknown protection outcome must not lock the position in ───────────

class _LiveClient(_FakeClient):
    _is_paper = False

    async def place_order_option(self, sym, side, size, **kw):
        from app.services.snapback_family_mode import INTENT_REDUCE, current_intent

        assert current_intent() == INTENT_REDUCE
        self.sells.append((sym, side, size))
        self.calls.append("sell")
        return {"order_id": "EXIT-LIVE"}


def _gtt_row(tid: int, *, status: str = "active", qty: int = 50, product: str = "NRML",
             symbol: str = "NIFTY24JUN24000CE", side: str = "SELL"):
    return {
        "id": tid, "status": status,
        "condition": {"tradingsymbol": symbol, "exchange": "NFO", "trigger_values": [80.0]},
        "orders": [{"tradingsymbol": symbol, "exchange": "NFO", "transaction_type": side,
                    "quantity": qty, "order_type": "LIMIT", "product": product}],
    }


def _pending_protection(uid: str, *, qty: int = 50):
    p = _held(uid, gtt_id=0, qty=qty)
    p.product = "NRML"
    p.protection_pending = True
    pos.persist_strict(uid)
    return p


@pytest.mark.asyncio
async def test_unknown_protection_outcome_does_not_block_an_exit_when_the_book_is_readable():
    p = _pending_protection("p3-unk")
    client = _LiveClient(gtt_book=[_gtt_row(77), _gtt_row(78, symbol="OTHER24JUN1CE")])

    sent = await monitor._exit_position(client, "p3-unk", p, 70.0, reason="trail breach")

    assert sent is True
    assert client.cancelled == [77], "the resting trigger of unknown ownership is removed first"
    assert client.sells == [(p.symbol, "sell", 50)]
    assert pos.get("p3-unk", p.symbol).protection_pending is False


@pytest.mark.asyncio
async def test_unknown_protection_outcome_blocks_when_the_trigger_already_fired():
    p = _pending_protection("p3-fired")
    client = _LiveClient(gtt_book=[_gtt_row(77, status="triggered")])

    sent = await monitor._exit_position(client, "p3-fired", p, 70.0, reason="trail breach")

    assert sent is False
    assert client.sells == [], "its own SELL is already out; never a second one"
    assert client.cancelled == []


@pytest.mark.asyncio
async def test_unreadable_book_blocks_an_ordinary_exit_but_not_an_emergency_one():
    from app.services.kite_engine import service as ksvc

    p = _pending_protection("p3-blind")
    client = _LiveClient(book_error="gtts")

    assert await monitor._exit_position(client, "p3-blind", p, 70.0, reason="trail breach") is False
    assert client.sells == []

    result = await ksvc.emergency_square_off_all(client, "p3-blind")

    assert client.sells == [(p.symbol, "sell", 50)], "operator emergency must still get out"
    assert result["submitted_awaiting_fill"] == 1


@pytest.mark.asyncio
async def test_emergency_square_off_clears_unknown_protection_through_the_book():
    from app.services.kite_engine import service as ksvc

    p = _pending_protection("p3-emerg")
    client = _LiveClient(gtt_book=[_gtt_row(91)])

    result = await ksvc.emergency_square_off_all(client, "p3-emerg")

    assert client.cancelled == [91]
    assert client.sells == [(p.symbol, "sell", 50)]
    assert result["status"] == "PENDING_BROKER_CONFIRMATION"


@pytest.mark.asyncio
async def test_definitive_broker_refusal_of_a_stop_does_not_latch_protection():
    """A 4xx proves no trigger was created, so nothing is 'unknown' and a retry is safe."""
    from app.services.exchanges.kite.errors import KiteInputError
    from app.services.kite_engine import execution_lifecycle as life

    p = _held("p3-refused", gtt_id=0)
    p.product = "NRML"
    client = _LiveClient()

    async def refuse(**kw):
        raise KiteInputError("Invalid trigger values", error_type="InputException",
                             status_code=400)

    client.place_gtt = refuse
    await life._protect(client, p)

    held = pos.get("p3-refused", p.symbol)
    assert held.gtt_id == 0
    assert held.protection_pending is False, "a refused request must not latch the position"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "5xx"])
async def test_ambiguous_stop_outcome_still_latches_protection(failure):
    import httpx

    from app.services.exchanges.kite.errors import KiteNetworkError
    from app.services.kite_engine import execution_lifecycle as life

    p = _held("p3-ambig-" + failure, gtt_id=0)
    p.product = "NRML"
    client = _LiveClient()

    async def ambiguous(**kw):
        if failure == "timeout":
            raise httpx.ReadTimeout("read timed out")
        raise KiteNetworkError("OMS unreachable", error_type="NetworkException", status_code=503)

    client.place_gtt = ambiguous
    await life._protect(client, p)

    assert pos.get("p3-ambig-" + failure, p.symbol).protection_pending is True

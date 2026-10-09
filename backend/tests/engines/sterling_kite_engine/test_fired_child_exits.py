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
    monitor._exit_refused_at.clear()
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


# ── finding 12: a definitive refusal of the exit SELL must not strand the position ──

class _RefusingExitClient(_FakeClient):
    _is_paper = False

    def __init__(self, error: BaseException, **kw):
        super().__init__(**kw)
        self.error = error
        self.attempts = 0

    async def place_order_option(self, sym, side, size, **kw):
        self.attempts += 1
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    KiteOrderError("RMS: insufficient margin", status_code=400, error_type="OrderException"),
    KiteOrderError("Too many requests", status_code=429, error_type="NetworkException"),
    PermissionError("Broker write guard: exit_order refused"),
])
async def test_a_definitive_refusal_releases_the_exit_and_rearms_the_stop(error):
    p = _position("rf-def")
    client = _RefusingExitClient(error)

    sold = await monitor._exit_position(client, "rf-def", p, 79.0, price_stop_exit=True)

    assert sold is False
    held = pos.get("rf-def", SYMBOL)
    assert held.exit_order_id == "" and held.exit_tag == ""
    assert held.exit_requested_ms == 0 and held.exit_requested_qty == 0
    assert held.exit_reason == ""
    assert client.gtts, "the stop the exit had cancelled must be put back"
    assert held.gtt_id == 4242 and held.protection_pending is False
    # and the position stays managed: a later exit attempt is allowed to try again
    assert await monitor._exit_position(client, "rf-def", held, 70.0, price_stop_exit=True) is False
    assert client.attempts == 2


@pytest.mark.asyncio
async def test_an_ambiguous_failure_of_the_exit_sell_still_latches_unknown():
    import httpx

    p = _position("rf-amb")
    client = _RefusingExitClient(httpx.ReadTimeout("read timed out"))

    sold = await monitor._exit_position(client, "rf-amb", p, 79.0, price_stop_exit=True)

    assert sold is False
    held = pos.get("rf-amb", SYMBOL)
    assert held.exit_order_id == "unknown", "the SELL may have reached the broker"
    assert not client.gtts, "never re-arm a stop over an exit that may be live"
    assert await monitor._exit_position(client, "rf-amb", held, 70.0, price_stop_exit=True) is False
    assert client.attempts == 1, "no blind resend"


# ── findings 5 + 10: _clear_unknown_protection is child-aware and ownership-aware ──

def _gtt_row(tid: int, *, status: str = "active", qty: int = 50, created_s_ago: float | None = None,
             symbol: str = SYMBOL, side: str = "SELL", child_id: str = ""):
    row = {
        "id": tid, "status": status,
        "condition": {"tradingsymbol": symbol, "exchange": "NFO", "trigger_values": [80.0]},
        "orders": [{"tradingsymbol": symbol, "exchange": "NFO", "transaction_type": side,
                    "quantity": qty, "order_type": "LIMIT", "product": "NRML"}],
    }
    if created_s_ago is not None:
        row["created_at"] = _stamp(created_s_ago)
        row["updated_at"] = _stamp(created_s_ago)
    if child_id:
        row["orders"][0]["result"] = {
            "timestamp": _stamp(created_s_ago or 0.0),
            "order_result": {"order_id": child_id, "status": "success", "rejection_reason": ""}}
    return row


class _BookClient(_FiredChildClient):
    """The GTT book is what ``_clear_unknown_protection`` reads; the child is what
    ``stop_state`` reads for a fired row."""

    def __init__(self, rows, **kw):
        super().__init__(**kw)
        self.gtt_book = list(rows)
        self.deleted: list = []

    async def get_gtt(self, tid):
        self.calls.append("get_gtt")
        for row in self.gtt_book:
            if row["id"] == tid:
                return dict(row)
        raise KeyError(tid)

    async def delete_gtt(self, tid):
        self.calls.append("cancel")
        self.deleted.append(tid)
        raise RuntimeError("delete must never be attempted without ownership proof")


def _pending(uid: str, *, qty: int = 50, opened_s_ago: float = 3600.0):
    p = _held(uid, gtt_id=0, qty=qty, stop=80.0)
    p.product = "NRML"
    p.protection_pending = True
    p.opened_ms = int((time.time() - opened_s_ago) * 1000)
    pos.persist_strict(uid)
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize("forced", [False, True])
async def test_a_triggered_row_from_before_this_position_never_blocks_the_exit(forced):
    p = _pending("cu-hist")
    client = _BookClient([_gtt_row(111, status="triggered", created_s_ago=86_400)])

    sold = await monitor._exit_position(client, "cu-hist", p, 60.0, forced=forced,
                                        reason="emergency_square_off" if forced else "time stop")

    assert sold is True, "an earlier round trip's fired trigger is not this position's exit"
    assert client.sells == [(SYMBOL, "sell", 50)]
    assert client.deleted == [], "a trigger is never deleted without an id-based proof"
    assert pos.get("cu-hist", SYMBOL).protection_pending is False


@pytest.mark.asyncio
async def test_a_rejected_child_of_a_fired_row_does_not_block_the_exit():
    p = _pending("cu-rej")
    row = _gtt_row(112, status="triggered", created_s_ago=600, child_id="CHILD-1")
    client = _BookClient([row])
    client.child["status"] = "REJECTED"

    sold = await monitor._exit_position(client, "cu-rej", p, 60.0, forced=True,
                                        reason="emergency_square_off")

    assert sold is True
    assert client.sells == [(SYMBOL, "sell", 50)]
    assert client.cancelled_orders == [], "a dead child needs no cancel"


@pytest.mark.asyncio
async def test_a_plausible_fired_row_with_a_stale_child_is_cleared_for_a_forced_exit():
    p = _pending("cu-stale")
    client = _BookClient([_gtt_row(113, status="triggered", created_s_ago=600, child_id="CHILD-1")],
                         child_age_s=600)

    sold = await monitor._exit_position(client, "cu-stale", p, 60.0, forced=True,
                                        reason="emergency_square_off")

    assert sold is True
    assert client.cancelled_orders == ["CHILD-1"]
    assert client.sells == [(SYMBOL, "sell", 50)]
    assert client.deleted == []


@pytest.mark.asyncio
async def test_a_plausible_fired_row_whose_child_filled_is_the_exit():
    p = _pending("cu-done")
    client = _BookClient([_gtt_row(114, status="triggered", created_s_ago=600, child_id="CHILD-1")])
    client.child["status"] = "COMPLETE"
    client.child["filled_quantity"] = 50

    sold = await monitor._exit_position(client, "cu-done", p, 60.0, forced=True,
                                        reason="emergency_square_off")

    assert sold is False and client.sells == [], "its own SELL is out; never a second one"


@pytest.mark.asyncio
async def test_an_unowned_active_trigger_is_never_deleted_and_holds_an_ordinary_exit():
    p = _pending("cu-act")
    client = _BookClient([_gtt_row(77, created_s_ago=600)])

    sold = await monitor._exit_position(client, "cu-act", p, 70.0, reason="time stop")

    assert sold is False and client.sells == []
    assert client.deleted == []
    assert pos.get("cu-act", SYMBOL).protection_pending is True, "the fence stays"


@pytest.mark.asyncio
async def test_an_unowned_active_trigger_does_not_stop_a_forced_exit_but_is_never_deleted():
    from app.services.kite_engine import service as ksvc

    p = _pending("cu-act-forced")
    client = _BookClient([_gtt_row(78, created_s_ago=600)])

    result = await ksvc.emergency_square_off_all(client, "cu-act-forced")

    assert client.sells == [(SYMBOL, "sell", 50)]
    assert client.deleted == []
    assert result["submitted_awaiting_fill"] == 1
    held = pos.get("cu-act-forced", SYMBOL)
    assert held.protection_pending is True, (
        "a trigger that may be this position's stays fenced: a rejected exit must not "
        "arm a rival stop")


@pytest.mark.asyncio
async def test_another_quantitys_active_trigger_is_left_alone_and_does_not_block():
    p = _pending("cu-oth")
    client = _BookClient([_gtt_row(79, qty=25, created_s_ago=600)])

    sold = await monitor._exit_position(client, "cu-oth", p, 70.0, reason="time stop")

    assert sold is True
    assert client.deleted == []
    assert pos.get("cu-oth", SYMBOL).protection_pending is False


# ── finding 6: a forced exit that rested and was cancelled must be re-submitted ──

class _RestingSellClient(_FakeClient):
    """Every exit SELL rests OPEN until the engine cancels it."""

    _is_paper = False

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cancelled_orders: list = []
        self.states: dict = {}

    async def place_order_option(self, sym, side, size, **kw):
        n = len(self.sells) + 1
        self.sells.append((sym, side, size))
        self.calls.append("sell")
        self.states[f"EXIT-{n}"] = "OPEN"
        return {"order_id": f"EXIT-{n}"}

    async def cancel_order(self, order_id, *a, **kw):
        self.cancelled_orders.append(order_id)
        self.states[order_id] = "CANCELLED"
        return {"order_id": order_id}

    async def get_order_history(self, order_id):
        return [{"order_id": order_id, "status": self.states.get(order_id, "OPEN"),
                 "tradingsymbol": SYMBOL, "exchange": "NFO", "transaction_type": "SELL",
                 "quantity": 50, "filled_quantity": 0, "average_price": 0}]


async def _rest_and_cancel(client, uid, *, forced: bool, reason: str):
    p = _position(uid)
    sent = await monitor._exit_position(client, uid, p, 70.0, forced=forced, reason=reason,
                                        price_stop_exit=False)
    assert sent is True
    cur = pos.get(uid, SYMBOL)
    return cur, await monitor.resolve_resting_exit(
        client, uid, cur, now_ms=int(cur.exit_requested_ms) + 9_000)


@pytest.mark.asyncio
async def test_a_forced_exit_that_rested_and_was_cancelled_is_resubmitted():
    client = _RestingSellClient()

    cur, attempted = await _rest_and_cancel(client, "fx-forced", forced=True,
                                            reason="emergency_square_off")

    assert attempted is True
    assert client.cancelled_orders == ["EXIT-1"]
    assert len(client.sells) == 2, "the operator's flatten must be tried again, not dropped"
    held = pos.get("fx-forced", SYMBOL)
    assert held.exit_order_id == "EXIT-2" and held.qty == 50


@pytest.mark.asyncio
async def test_an_ordinary_exit_that_rested_is_not_resubmitted():
    client = _RestingSellClient()

    cur, attempted = await _rest_and_cancel(client, "fx-plain", forced=False,
                                            reason="trail breach")

    assert attempted is True
    assert len(client.sells) == 1, "an ordinary exit re-evaluates on the next tick"
    held = pos.get("fx-plain", SYMBOL)
    assert held.exit_order_id == "" and held.gtt_id == 4242, "stop is back"


@pytest.mark.asyncio
async def test_forced_resubmission_is_bounded_and_then_escalates():
    client = _RestingSellClient()
    cur, _ = await _rest_and_cancel(client, "fx-bound", forced=True, reason="emergency_square_off")
    for _ in range(monitor._FORCED_RESUBMIT_MAX + 2):
        held = pos.get("fx-bound", SYMBOL)
        if not held.exit_order_id:
            break
        await monitor.resolve_resting_exit(
            client, "fx-bound", held, now_ms=int(held.exit_requested_ms) + 9_000)

    assert len(client.sells) == 1 + monitor._FORCED_RESUBMIT_MAX
    held = pos.get("fx-bound", SYMBOL)
    assert held.exit_order_id == "" and held.status == pos.OPEN, "left open, stop re-armed"


# ── finding 13: recover() must not arm a stop over a live exit order ──────────

@pytest.mark.asyncio
@pytest.mark.parametrize("exit_id", ["EXIT-1", "submitting", "unknown"])
async def test_recover_never_arms_a_stop_while_an_exit_order_is_live(exit_id):
    from app.services.kite_engine import execution_lifecycle as lifecycle

    p = _held("rc-live", gtt_id=0)
    p.product = "NRML"
    p.stop_mode = "both"
    p.exit_order_id = exit_id
    p.exit_tag = "kxlive"
    p.exit_requested_qty = 50
    p.exit_requested_ms = int(time.time() * 1000) - 3_000
    pos.persist_strict("rc-live")
    client = _RestingSellClient()

    await lifecycle.recover(client, "rc-live")

    held = pos.get("rc-live", SYMBOL)
    assert client.gtts == [], "a stop over a working exit sells the position twice"
    assert held.gtt_id == 0 and held.exit_order_id == exit_id


@pytest.mark.asyncio
async def test_recover_still_repairs_a_missing_stop_when_no_exit_is_live():
    from app.services.kite_engine import execution_lifecycle as lifecycle

    p = _held("rc-none", gtt_id=0)
    p.product = "NRML"
    p.stop_mode = "both"
    pos.persist_strict("rc-none")
    client = _RestingSellClient()

    await lifecycle.recover(client, "rc-none")

    assert client.gtts and pos.get("rc-none", SYMBOL).gtt_id == 4242


@pytest.mark.asyncio
async def test_a_fired_stop_whose_child_cannot_be_identified_holds_even_a_forced_exit():
    """Fail closed: a SELL over an order we cannot see may sell the position twice."""
    p = _position("fc-blind")
    client = _FiredChildClient()
    client.child.pop("product")          # identity incomplete: the book cannot attribute it

    sold = await monitor._exit_position(client, "fc-blind", p, 60.0, forced=True,
                                        reason="emergency_square_off")

    assert sold is False
    assert client.sells == [] and client.cancelled_orders == []

"""A triggered GTT has only fired: its LIMIT child may be filled, resting or rejected.

Reported defect: ``stop_status`` read every ``triggered`` GTT as "the exit is done",
so a child that rested unfilled in a gap (or was rejected by the exchange/RMS while
the GTT stayed ``triggered``) left every caller standing down forever on a position
that was never sold. These tests pin the child-aware verdicts, and the placement
outcome that tells a caller whether a failed POST may have created a trigger.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.exchanges.kite.errors import KiteInputError, KiteOrderError
from app.services.kite_engine import protective_stop as ps

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 8, 11, 0, 0, tzinfo=IST)
SYMBOL = "NIFTY26OCT24000CE"
IDENT = dict(tradingsymbol=SYMBOL, direction="long", exchange="NFO", product="NRML", qty=75)


def _stamp(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _gtt(*, status="triggered", result="auto", seconds_ago=120.0, trigger_id=555):
    if result == "auto":
        result = {
            "account_id": "AA0595", "exchange": "NFO", "tradingsymbol": SYMBOL,
            "product": "NRML", "timestamp": _stamp(seconds_ago),
            "order_result": {"order_id": "CHILD-1", "status": "success", "rejection_reason": ""},
        }
    return {
        "id": trigger_id, "status": status, "type": "single",
        "updated_at": _stamp(seconds_ago),
        "condition": {"exchange": "NFO", "tradingsymbol": SYMBOL, "trigger_values": [90.0]},
        "orders": [{
            "exchange": "NFO", "tradingsymbol": SYMBOL, "product": "NRML",
            "transaction_type": "SELL", "quantity": 75, "order_type": "LIMIT",
            "price": 88.2, "result": result,
        }],
    }


def _order(status, *, order_id="CHILD-1", seconds_ago=120.0, price=88.2, filled=0, qty=75):
    return {
        "order_id": order_id, "tradingsymbol": SYMBOL, "exchange": "NFO",
        "product": "NRML", "transaction_type": "SELL", "status": status,
        "quantity": qty, "filled_quantity": filled, "price": price,
        "order_timestamp": _stamp(seconds_ago),
    }


class _Client:
    _is_paper = False
    _account_id = "AA0595"

    def __init__(self, *, gtt=None, gtt_error=None, listed=None, orders=(), orders_error=None,
                 history=None):
        self._gtt, self._gtt_error, self._listed = gtt, gtt_error, listed
        self._orders, self._orders_error, self._history = list(orders), orders_error, history
        self.calls: list[str] = []

    async def get_gtt(self, trigger_id):
        self.calls.append("get_gtt")
        if self._gtt_error:
            raise self._gtt_error
        return self._gtt

    async def get_gtts(self):
        self.calls.append("get_gtts")
        return list(self._listed or [])

    async def get_orders(self):
        self.calls.append("get_orders")
        if self._orders_error:
            raise self._orders_error
        return list(self._orders)

    async def get_order_history(self, order_id):
        self.calls.append("get_order_history")
        if self._history is None:
            raise RuntimeError("history unavailable")
        return list(self._history)


async def _state(client, **overrides):
    kwargs = {**IDENT, "now": NOW, **overrides}
    return await ps.stop_state(client, 555, **kwargs)


# --------------------------------------------------------------- fired triggers
@pytest.mark.asyncio
async def test_triggered_with_a_filled_child_is_the_only_triggered_verdict():
    state = await _state(_Client(gtt=_gtt(), orders=[_order("COMPLETE", filled=75)]))
    assert state.verdict == ps.STOP_TRIGGERED
    assert state.child_state == ps.CHILD_FILLED
    assert state.child_order_id == "CHILD-1"
    assert state.child_filled_quantity == 75
    assert state.fired is True


@pytest.mark.asyncio
async def test_triggered_with_a_child_resting_past_the_grace_window_is_stale():
    state = await _state(_Client(gtt=_gtt(), orders=[_order("OPEN", seconds_ago=120.0)]))
    assert state.verdict == ps.STOP_TRIGGERED_STALE
    assert state.child_state == ps.CHILD_RESTING
    assert state.child_order_id == "CHILD-1"
    assert state.child_price == pytest.approx(88.2)
    assert state.fired is True


@pytest.mark.asyncio
async def test_triggered_with_a_fresh_child_is_resting_not_stale():
    state = await _state(
        _Client(gtt=_gtt(seconds_ago=5.0), orders=[_order("OPEN", seconds_ago=5.0)]))
    assert state.verdict == ps.STOP_TRIGGERED_RESTING
    assert state.child_state == ps.CHILD_RESTING


@pytest.mark.asyncio
async def test_a_fresh_child_whose_limit_is_above_the_market_is_stale_immediately():
    """A gap: the SELL limit sits above where the option now trades. Waiting out the
    grace window only lengthens the time the position is unprotected."""
    state = await _state(
        _Client(gtt=_gtt(seconds_ago=5.0), orders=[_order("OPEN", seconds_ago=5.0)]),
        reference_price=80.0,
    )
    assert state.verdict == ps.STOP_TRIGGERED_STALE


@pytest.mark.asyncio
async def test_a_partially_filled_working_child_is_still_a_working_child():
    state = await _state(
        _Client(gtt=_gtt(seconds_ago=5.0),
                orders=[_order("OPEN", seconds_ago=5.0, filled=25)]))
    assert state.verdict == ps.STOP_TRIGGERED_RESTING
    assert state.child_filled_quantity == 25


@pytest.mark.asyncio
async def test_a_child_rejected_in_the_order_book_means_nothing_will_exit_us():
    state = await _state(_Client(gtt=_gtt(), orders=[_order("REJECTED")]))
    assert state.verdict == ps.STOP_ABSENT
    assert state.child_state == ps.CHILD_REJECTED
    assert state.fired is False


@pytest.mark.asyncio
async def test_a_cancelled_child_means_nothing_will_exit_us():
    state = await _state(_Client(gtt=_gtt(), orders=[_order("CANCELLED")]))
    assert state.verdict == ps.STOP_ABSENT


@pytest.mark.asyncio
async def test_a_placement_the_gtt_itself_reports_as_failed_is_absent():
    result = {
        "timestamp": _stamp(60),
        "order_result": {"order_id": "", "status": "failed",
                         "rejection_reason": "RMS: insufficient margin"},
    }
    state = await _state(_Client(gtt=_gtt(result=result), orders=[]))
    assert state.verdict == ps.STOP_ABSENT
    assert state.child_state == ps.CHILD_REJECTED
    assert "margin" in state.detail


@pytest.mark.asyncio
async def test_a_triggered_gtt_with_no_child_after_the_grace_window_is_absent():
    state = await _state(_Client(gtt=_gtt(result=None, seconds_ago=120.0), orders=[]))
    assert state.verdict == ps.STOP_ABSENT
    assert state.child_state == ps.CHILD_MISSING


@pytest.mark.asyncio
async def test_a_triggered_gtt_whose_child_is_not_visible_yet_stands_down_briefly():
    """The order is placed a moment after the status flips. Selling now could double up."""
    state = await _state(_Client(gtt=_gtt(result=None, seconds_ago=3.0), orders=[]))
    assert state.verdict == ps.STOP_TRIGGERED_RESTING
    assert state.child_state == ps.CHILD_MISSING


@pytest.mark.asyncio
async def test_without_a_child_result_the_book_is_matched_on_full_identity():
    state = await _state(_Client(
        gtt=_gtt(result=None),
        orders=[_order("COMPLETE", order_id="OTHER-9", filled=75)],
    ))
    assert state.verdict == ps.STOP_TRIGGERED
    assert state.child_order_id == "OTHER-9"


@pytest.mark.asyncio
async def test_a_child_sized_differently_from_what_we_hold_is_still_the_child():
    """The GTT was armed for 75; the registry was resized to 50 since. Selling 50 on
    top of a filled 75 would be the naked short."""
    state = await _state(
        _Client(gtt=_gtt(result=None, seconds_ago=60.0),
                orders=[_order("COMPLETE", order_id="OTHER-9", filled=75, seconds_ago=50.0)]),
        qty=50,
    )
    assert state.verdict == ps.STOP_TRIGGERED


@pytest.mark.asyncio
async def test_a_buy_in_the_book_is_not_mistaken_for_the_child():
    buy = {**_order("COMPLETE", order_id="ENTRY-1", filled=75), "transaction_type": "BUY"}
    state = await _state(_Client(gtt=_gtt(result=None, seconds_ago=120.0), orders=[buy]))
    assert state.verdict == ps.STOP_ABSENT


@pytest.mark.asyncio
async def test_an_unreadable_order_book_is_unverified_never_absent():
    state = await _state(_Client(gtt=_gtt(), orders_error=RuntimeError("book down")))
    assert state.verdict == ps.STOP_UNVERIFIED


@pytest.mark.asyncio
async def test_a_child_id_that_cannot_be_found_anywhere_is_unverified():
    state = await _state(_Client(gtt=_gtt(), orders=[], history=None))
    assert state.verdict == ps.STOP_UNVERIFIED
    assert state.child_state == ps.CHILD_UNKNOWN


@pytest.mark.asyncio
async def test_a_child_missing_from_the_book_is_read_from_its_order_history():
    state = await _state(_Client(
        gtt=_gtt(), orders=[],
        history=[{"status": "OPEN", "quantity": 75, "price": 88.2}, {
            "status": "COMPLETE", "quantity": 75, "filled_quantity": 75, "price": 88.2}],
    ))
    assert state.verdict == ps.STOP_TRIGGERED
    assert state.child_order_id == "CHILD-1"


@pytest.mark.asyncio
async def test_callers_without_identity_stand_down_but_are_not_told_it_filled():
    client = _Client(gtt=_gtt(result=None), orders=[])
    verdict = await ps.stop_status(client, 555)
    assert verdict == ps.STOP_TRIGGERED_RESTING
    assert verdict in ps.FIRED_VERDICTS and verdict != ps.STOP_TRIGGERED


@pytest.mark.asyncio
async def test_the_trigger_list_row_is_resolved_the_same_way():
    class _ListOnly(_Client):
        async def get_gtt(self, trigger_id):
            self.calls.append("get_gtt")
            raise RuntimeError("400 InputException")

    client = _ListOnly(listed=[_gtt()], orders=[_order("OPEN", seconds_ago=120.0)])
    assert (await _state(client)).verdict == ps.STOP_TRIGGERED_STALE


@pytest.mark.asyncio
async def test_an_active_trigger_never_reads_the_order_book():
    client = _Client(gtt=_gtt(status="active", result=None))
    state = await _state(client)
    assert state.verdict == ps.STOP_ACTIVE
    assert "get_orders" not in client.calls


# ----------------------------------------------- trigger no longer on the book
class _Gone(Exception):
    status_code = 404


@pytest.mark.asyncio
async def test_a_vanished_trigger_with_an_old_working_exit_order_is_stale():
    client = _Client(gtt_error=_Gone("not found"), orders=[_order("OPEN", seconds_ago=300.0)])
    assert (await _state(client)).verdict == ps.STOP_TRIGGERED_STALE


@pytest.mark.asyncio
async def test_a_vanished_trigger_with_a_filled_exit_order_is_triggered():
    client = _Client(gtt_error=_Gone("not found"), orders=[_order("COMPLETE", filled=75)])
    assert (await _state(client)).verdict == ps.STOP_TRIGGERED


@pytest.mark.asyncio
async def test_a_vanished_trigger_with_only_a_rejected_exit_order_is_absent():
    client = _Client(gtt_error=_Gone("not found"), orders=[_order("REJECTED")])
    assert (await _state(client)).verdict == ps.STOP_ABSENT


@pytest.mark.asyncio
async def test_the_callers_own_exit_order_can_be_excluded_from_attribution():
    own = _order("OPEN", order_id="MY-EXIT", seconds_ago=300.0)
    client = _Client(gtt_error=_Gone("not found"), orders=[own])
    state = await _state(client, ignore_order_ids=frozenset({"MY-EXIT"}))
    assert state.verdict == ps.STOP_ABSENT


@pytest.mark.asyncio
async def test_stop_status_returns_the_same_verdict_as_stop_state():
    client = _Client(gtt=_gtt(), orders=[_order("OPEN", seconds_ago=120.0)])
    assert await ps.stop_status(client, 555, **IDENT) == ps.STOP_TRIGGERED_STALE


# ----------------------------------------------------- place_stop_outcome (#1)
class _Placer:
    """Live broker double for ``place_stop_outcome``."""

    _is_paper = False
    _account_id = "AA0595"

    def __init__(self, *, place):
        self._place = place
        self.posts = 0

    async def get_positions_raw(self):
        return {"net": [{"tradingsymbol": SYMBOL, "exchange": "NFO", "product": "NRML",
                         "quantity": 75}]}

    async def get_ltp(self, keys):
        return {k: {"last_price": 120.0} for k in keys}

    async def place_gtt(self, **kwargs):
        self.posts += 1
        return self._place()


def _raise(exc):
    def _inner():
        raise exc
    return _inner


async def _place(client, **kw):
    from app.services.snapback_family_mode import INTENT_PROTECT, canonical_broker_capability

    with canonical_broker_capability("test-place-outcome", intent=INTENT_PROTECT):
        return await ps.place_stop_outcome(
            client, tradingsymbol=SYMBOL, exchange="NFO", qty=75, trigger_premium=90.0,
            last_price=100.0, direction="long", product="NRML", **kw,
        )


@pytest.mark.asyncio
async def test_a_timeout_after_the_post_is_ambiguous_and_latches_the_fence_first():
    import httpx

    fence: list[str] = []
    client = _Placer(place=_raise(httpx.ReadTimeout("10s")))
    outcome = await _place(client, before_submit=lambda: fence.append("latched"))
    assert fence == ["latched"] and client.posts == 1
    assert outcome.trigger_id is None
    assert outcome.ambiguous is True


@pytest.mark.asyncio
async def test_a_5xx_is_ambiguous():
    from app.services.exchanges.kite.errors import KiteError

    client = _Placer(place=_raise(KiteError("gateway", status_code=502)))
    assert (await _place(client)).ambiguous is True


@pytest.mark.asyncio
async def test_a_definitive_4xx_rejection_created_nothing_and_is_not_ambiguous():
    lifted: list[str] = []
    client = _Placer(place=_raise(KiteInputError("bad trigger", status_code=400)))
    outcome = await _place(client, on_refused=lambda: lifted.append("lifted"))
    assert outcome.trigger_id is None and outcome.ambiguous is False
    assert lifted == ["lifted"]


@pytest.mark.asyncio
async def test_an_order_rejection_is_not_ambiguous():
    client = _Placer(place=_raise(KiteOrderError("rejected", status_code=400)))
    assert (await _place(client)).ambiguous is False


@pytest.mark.asyncio
async def test_a_reply_without_a_trigger_id_is_ambiguous():
    client = _Placer(place=lambda: {"status": "ok"})
    assert (await _place(client)).ambiguous is True


@pytest.mark.asyncio
async def test_success_returns_the_id():
    client = _Placer(place=lambda: {"trigger_id": 4321})
    outcome = await _place(client)
    assert outcome.trigger_id == 4321 and outcome.ambiguous is False


@pytest.mark.asyncio
async def test_a_fence_that_cannot_be_latched_sends_nothing():
    client = _Placer(place=lambda: {"trigger_id": 1})
    outcome = await _place(client, before_submit=_raise(RuntimeError("disk full")))
    assert client.posts == 0
    assert outcome.trigger_id is None and outcome.ambiguous is False


@pytest.mark.asyncio
async def test_a_refusal_before_the_post_is_not_ambiguous():
    class _NoInventory(_Placer):
        async def get_positions_raw(self):
            return {"net": []}

    client = _NoInventory(place=lambda: {"trigger_id": 1})
    outcome = await _place(client)
    assert client.posts == 0
    assert outcome.ambiguous is False and outcome.trigger_id is None

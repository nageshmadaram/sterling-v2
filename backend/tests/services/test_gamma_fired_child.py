"""Gamma-Unwind exits against a fired GTT whose LIMIT child rests unfilled, and a
pending-placement fence that must only adopt a trigger of its own window.

Second adversarial review (Q2). The stop's LIMIT child sits under the trigger; in a
gap it rests OPEN while the GTT is consumed, so nothing at the broker will sell the
position. The exit must cancel the child (INTENT_CANCEL), book what it filled and
sell only what the broker still holds. An operator flatten does not wait out the grace.
"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.services.exchanges.kite.errors import KiteInputError
from app.services.kite_engine import protective_stop as pstop
from tests.services.test_gamma_recovery import (  # noqa: F401  (env is a fixture)
    NOW, SYMBOL, UID, _position, env, runner, store,
)


@pytest.fixture(autouse=True)
def _no_poll_sleep(monkeypatch):
    monkeypatch.setattr(pstop, "CHILD_CANCEL_POLL_S", 0.0)


def _stamp(seconds_before_now: float) -> str:
    from datetime import datetime
    from app.services.kite_engine.protective_stop import _IST

    return (datetime.now(_IST) - timedelta(seconds=seconds_before_now)).replace(
        tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _arm_fired(env, *, age_s: float = 300.0, filled_on_cancel: int = 0,
               cancel_lands: bool = True, final_status: str = "CANCELLED"):
    pos = _position()
    pos.gtt_id = 555
    pos.gtt_at = 90.0
    pos.gtt_qty = 50
    store.put(UID, pos)
    b = env.broker
    stamp = _stamp(age_s)
    b.gtts = [{
        "id": 555, "status": "triggered", "type": "single", "updated_at": stamp,
        "condition": {"exchange": "NFO", "tradingsymbol": SYMBOL, "trigger_values": [90.0]},
        "orders": [{"exchange": "NFO", "tradingsymbol": SYMBOL, "product": "NRML",
                    "transaction_type": "SELL", "quantity": 50, "order_type": "LIMIT",
                    "price": 88.2,
                    "result": {"timestamp": stamp, "order_result": {
                        "order_id": "CHILD-1", "status": "success", "rejection_reason": ""}}}],
    }]
    b.orders = [{"order_id": "CHILD-1", "tradingsymbol": SYMBOL, "exchange": "NFO",
                 "product": "NRML", "transaction_type": "SELL", "status": "OPEN",
                 "quantity": 50, "filled_quantity": 0, "average_price": 0.0,
                 "price": 88.2, "order_timestamp": stamp}]
    b.child_cancels = []

    async def bad_delete(tid):
        raise KiteInputError("Trigger is already triggered", status_code=400)

    async def cancel_order(order_id, **_kw):
        from app.services.snapback_family_mode import INTENT_CANCEL, current_intent

        assert current_intent() == INTENT_CANCEL, "child cancelled without CANCEL authority"
        b.child_cancels.append(order_id)
        if cancel_lands:
            for o in b.orders:
                if o["order_id"] == order_id:
                    o["status"] = final_status
                    if filled_on_cancel:
                        o["filled_quantity"] = filled_on_cancel
                        o["average_price"] = 87.0
                        b.net_qty -= filled_on_cancel
        return {"order_id": order_id}

    b.delete_gtt = bad_delete
    b.cancel_order = cancel_order
    b.ltp = 70.0   # gapped far below the child's limit
    return pos


@pytest.mark.asyncio
async def test_stale_child_is_cancelled_and_the_position_is_sold(env):
    pos = _arm_fired(env)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=70.0, reason="SL")

    b = env.broker
    assert b.child_cancels == ["CHILD-1"]
    assert len(b.place_order_calls) == 1 and b.place_order_calls[0]["qty"] == 50.0
    assert ok is True and status == "closed"


@pytest.mark.asyncio
async def test_part_filled_child_is_booked_and_only_the_rest_is_sold(env):
    pos = _arm_fired(env, filled_on_cancel=20)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=70.0, reason="SL")

    b = env.broker
    assert b.child_cancels == ["CHILD-1"]
    assert [c["qty"] for c in b.place_order_calls] == [30.0], "20 already sold by the child"
    assert ok is True
    # 20 @ 87 (child) and 30 @ 88 (market exit) against an entry of 100
    expected = (87.0 - 100.0) * 20 + (88.0 - 100.0) * 30
    assert env.record.realised_inr == pytest.approx(expected)


@pytest.mark.asyncio
async def test_a_child_inside_the_grace_window_is_left_to_fill(env):
    pos = _arm_fired(env, age_s=3.0)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=89.0, reason="SL")

    assert ok is False and status == "protection_not_released"
    assert env.broker.child_cancels == [] and env.broker.place_order_calls == []


@pytest.mark.asyncio
async def test_operator_flatten_does_not_wait_out_the_grace_window(env):
    _arm_fired(env, age_s=3.0)

    out = await runner.flatten_all(UID, env.broker, reason="operator")

    b = env.broker
    assert b.child_cancels == ["CHILD-1"]
    assert len(b.place_order_calls) == 1
    assert out["closed"] == 1 and out["failed"] == []


@pytest.mark.asyncio
async def test_a_child_whose_cancel_is_unconfirmed_blocks_the_sell(env):
    pos = _arm_fired(env, cancel_lands=False)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=70.0, reason="SL")

    assert ok is False and status == "protection_not_released"
    assert env.broker.place_order_calls == []
    assert pos.gtt_id == 555, "the consumed trigger stays on file until the child is dead"


@pytest.mark.asyncio
async def test_a_child_that_fills_before_the_cancel_lands_is_the_exit(env):
    pos = _arm_fired(env, final_status="COMPLETE", filled_on_cancel=50)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=70.0, reason="SL")

    assert ok is False
    assert env.broker.place_order_calls == [], "its SELL completed; never a second one"


# ── finding 11: a pending placement adopts only a trigger of its own window ──

def _row(tid: int, *, status: str, stamp_delta: timedelta | None, trigger: float = 90.0,
         qty: int = 50):
    row = {
        "id": tid, "status": status, "type": "single",
        "condition": {"exchange": "NFO", "tradingsymbol": SYMBOL, "trigger_values": [trigger]},
        "orders": [{"exchange": "NFO", "tradingsymbol": SYMBOL, "product": "NRML",
                    "transaction_type": "SELL", "quantity": qty, "order_type": "LIMIT",
                    "price": 88.2}],
    }
    if stamp_delta is not None:
        stamp = (NOW + stamp_delta).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        row["created_at"] = stamp
        row["updated_at"] = stamp
    return row


async def _lose_the_reply(env, pos):
    """The new placement's reply is lost: the fence is latched at NOW."""
    store.put(UID, pos)
    env.broker.gtt_error_after_create = httpx.ReadTimeout("10s")
    assert await runner._ensure_protection(UID, env.broker, pos) is False
    env.broker.gtt_error_after_create = None
    assert pos.gtt_pending is True


@pytest.mark.asyncio
async def test_an_earlier_stop_out_is_not_adopted_and_the_real_stop_is_not_deleted(env):
    b = env.broker
    # an earlier Gamma stop-out today on the same contract, quantity and structural stop
    b.gtts.append(_row(50, status="triggered", stamp_delta=-timedelta(hours=2)))
    pos = _position()
    await _lose_the_reply(env, pos)           # the broker did create #101

    ok = await runner._ensure_protection(UID, b, pos)

    assert ok is True
    assert pos.gtt_id == 101, "the new stop, not the earlier stop-out"
    assert b.deleted == [], "an ACTIVE stop is never cancelled in favour of a fired one"
    assert [(g["id"], g["status"]) for g in b.gtts] == [(50, "triggered"), (101, "active")]


@pytest.mark.asyncio
async def test_an_old_fired_row_alone_does_not_stop_a_fresh_placement(env):
    b = env.broker
    b.gtts.append(_row(50, status="triggered", stamp_delta=-timedelta(hours=2)))
    pos = _position()
    store.put(UID, pos)
    b.gtt_error_before_create = httpx.ReadTimeout("lost before it was stored")
    assert await runner._ensure_protection(UID, b, pos) is False
    b.gtt_error_before_create = None
    env.clock["ms"] += 120_000                 # past the in-flight window

    ok = await runner._ensure_protection(UID, b, pos)

    assert ok is True
    assert pos.gtt_id not in (0, 50), "a fresh trigger is placed; the old row is not adopted"
    assert pos.gtt_pending is False


@pytest.mark.asyncio
async def test_a_fired_row_of_this_window_is_this_positions_and_active_is_preferred(env):
    b = env.broker
    pos = _position()
    await _lose_the_reply(env, pos)           # #101 was created
    b.gtts.append(_row(160, status="triggered", stamp_delta=timedelta(minutes=2)))

    ok = await runner._ensure_protection(UID, b, pos)

    assert ok is True
    assert pos.gtt_id == 101, "ACTIVE over TRIGGERED"
    assert b.deleted == []


@pytest.mark.asyncio
async def test_a_fired_row_of_this_window_alone_is_adopted_so_the_fired_stop_logic_runs(env):
    b = env.broker
    pos = _position()
    store.put(UID, pos)
    b.gtt_error_before_create = httpx.ReadTimeout("lost before it was stored")
    assert await runner._ensure_protection(UID, b, pos) is False
    b.gtt_error_before_create = None
    b.gtts.append(_row(160, status="triggered", stamp_delta=timedelta(minutes=2)))
    env.clock["ms"] += 120_000

    await runner._ensure_protection(UID, b, pos)

    assert pos.gtt_id == 160


@pytest.mark.asyncio
async def test_a_fired_row_with_no_timestamp_cannot_be_attributed_and_keeps_the_fence(env):
    b = env.broker
    b.gtts.append(_row(50, status="triggered", stamp_delta=None))
    pos = _position()
    store.put(UID, pos)
    b.gtt_error_before_create = httpx.ReadTimeout("lost before it was stored")
    assert await runner._ensure_protection(UID, b, pos) is False
    b.gtt_error_before_create = None
    env.clock["ms"] += 120_000

    ok = await runner._ensure_protection(UID, b, pos)

    assert ok is False and pos.gtt_pending is True and pos.gtt_id == 0
    assert len(b.place_gtt_calls) == 1 and b.deleted == []


@pytest.mark.asyncio
async def test_reconcile_says_so_when_a_fired_stops_child_is_stale_and_does_not_resize(env):
    pos = _arm_fired(env)
    env.broker.net_qty = 30                  # the child part-filled, then stopped filling

    await runner.reconcile(UID)

    assert pos.quantity == 50, "the registry keeps describing the whole position"
    assert env.broker.place_order_calls == [] and env.broker.child_cancels == []
    assert any(a.get("event_type") == "STOP TRIGGERED, SELL NOT FILLING"
               and a.get("severity") == "CRITICAL" for a in env.alerts)

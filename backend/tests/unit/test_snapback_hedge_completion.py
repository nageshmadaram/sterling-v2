"""The hedge leg: priced from a fresh futures quote, and retried without a human.

The hedge LIMIT used to be fixed at plan-build time off the futures quote of that
moment. A manual plan lives up to five minutes, so a 25-point NIFTY move left the
LIMIT unfillable, the hedge ended with zero fill, and the dispatcher then engaged the
global kill switch - which refused the very retry that would have completed the hedge.
"""
from __future__ import annotations

import pytest

from tests.snapback_live_harness import (
    FUTURE_SYMBOL, OPTION_SYMBOL, SWING_NOW, live_env, swing_row,
)
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env

FUT_KEY = f"NFO:{FUTURE_SYMBOL}"


def _hedge_orders(env):
    return [o for o in env.broker.placed if o["symbol"] == FUTURE_SYMBOL]


def _kill_switch_on() -> bool:
    from app.services import live_safety

    return bool(live_safety.kill_switch_state().get("enabled"))


def test_the_hedge_limit_is_priced_from_the_futures_quote_at_the_moment_of_the_send(env):
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    published = run(env.dispatcher.publish_from_signal(env.uid, row["signal_id"]))
    assert published["ok"], published
    planned = float(env.plan_store.get(published["plan_id"])["hedge_price_limit"])
    env.plan_store.arm(published["plan_id"], expected_revision=published["revision"],
                       idempotency_key="k1", armed_by=env.uid, mode="LIVE", now=SWING_NOW)
    env.broker.set_quote(FUT_KEY, 25025.0, 25026.0)                 # the future ran 25 points

    result = run(env.dispatcher.execute_plan(env.uid, published["plan_id"]))

    assert result.status == "OPEN", result
    (hedge,) = _hedge_orders(env)
    assert hedge["limit_price"] > planned                           # not the stale plan-time price
    assert hedge["limit_price"] == pytest.approx(25026.0 * 1.001, abs=0.06)


def test_a_hedge_that_does_not_fill_is_retried_without_engaging_the_kill_switch(env):
    env.broker.hedge_mode = "hang"                                  # the LIMIT rests, nothing fills
    env.rows = [swing_row(signal_id=_signal_id())]

    first = _cycle(env)

    assert not _kill_switch_on(), "a retryable zero-fill hedge must alert, not halt"
    assert [p["status"] for p in env.plan_store.list_in_flight(owner_uid=env.uid)] == ["RECONCILING"]
    assert env.positions.open_positions(env.uid)[0].protection_state == "ACTIVE"   # option is stopped

    env.broker.hedge_mode = "fill"                                  # the book is fillable again
    _cycle(env)

    assert env.plan_store.list_in_flight(owner_uid=env.uid) == []   # completed with nobody's help
    assert env.positions.open_positions(env.uid)[0].status == "OPEN"
    assert env.broker.net[("NFO", "NRML", FUTURE_SYMBOL)] == 75
    assert not _kill_switch_on()


def test_repeated_zero_fill_hedges_stop_retrying_and_halt_entries(env):
    from app.services import snapback_live_executor as ex

    env.broker.hedge_mode = "hang"
    env.rows = [swing_row(signal_id=_signal_id())]

    for _ in range(ex.HEDGE_ZERO_FILL_RETRIES + 3):
        _cycle(env)

    assert len(_hedge_orders(env)) <= ex.HEDGE_ZERO_FILL_RETRIES + 1     # the first send + the retries
    assert _kill_switch_on(), "after the retries are spent a human must look"

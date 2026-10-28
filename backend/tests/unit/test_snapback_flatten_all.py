"""The operator / safety flatten for Snapback (Square-Off All, kill switch).

Before this nothing could close a Snapback position on request: the emergency routes
walked the Supertrend registry only, and the exit manager's explicit entry point had
no caller. ``flatten_all`` closes every position through the exit manager's one
resumable procedure, serialised with the dispatcher's own cycle.
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


def _open(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    return env.positions.open_positions(env.uid)[0]


def _flatten(env, reason="operator"):
    from app.services import snapback_dispatcher, snapback_live_exits

    env.monkeypatch.setattr(snapback_dispatcher, "_dispatcher", env.dispatcher)
    return run(snapback_live_exits.flatten_all(env.uid, env.broker, reason=reason))


def _get(env, pos):
    return env.positions.get(pos.position_id)


def test_flatten_all_closes_the_option_then_the_hedge_and_removes_the_resting_stop(env):
    pos = _open(env)
    before = len(env.broker.placed)

    result = _flatten(env)

    assert result == {"closed": 1, "failed": [], "skipped": [], "cancelled_plans": []}
    sells = [(o["symbol"], o["side"]) for o in env.broker.placed[before:]]
    assert sells == [(OPTION_SYMBOL, "SELL"), (FUTURE_SYMBOL, "SELL")]
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "FLATTEN_OPERATOR"
    assert pos.gtt_id in env.broker.deleted_gtts and env.broker.gtts == {}
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def test_flatten_all_is_idempotent(env):
    _open(env)
    _flatten(env)
    sent = len(env.broker.placed)

    again = _flatten(env)

    assert again["closed"] == 0 and again["failed"] == []
    assert len(env.broker.placed) == sent


def test_flatten_all_is_not_blocked_by_the_kill_switch(env):
    from app.services import live_safety

    pos = _open(env)
    live_safety.set_kill_switch(True, "test: emergency")

    result = _flatten(env, reason="kill_switch")

    assert result["closed"] == 1 and _get(env, pos).exit_reason == "FLATTEN_KILL_SWITCH"


@pytest.mark.kite_features('default')
def test_a_hedge_that_will_not_close_is_reported_and_finished_by_the_next_flatten(env):
    pos = _open(env)
    env.broker.hedge_mode = "reject_order"

    first = _flatten(env)

    assert first["closed"] == 0 and len(first["failed"]) == 1
    assert OPTION_SYMBOL in first["failed"][0]
    held = _get(env, pos)
    assert held.status == "EXITING" and held.option_exit_qty == 75 and held.hedge_exit_qty == 0
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0     # the option is gone

    env.broker.hedge_mode = "fill"
    second = _flatten(env)

    assert second["closed"] == 1 and second["failed"] == []
    assert _get(env, pos).status == "CLOSED"
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


@pytest.mark.kite_features('default')
def test_an_option_sell_that_is_refused_restores_the_broker_stop(env):
    pos = _open(env)
    env.broker.mode = "reject_order"

    result = _flatten(env)

    assert result["closed"] == 0 and len(result["failed"]) == 1
    held = _get(env, pos)
    assert held.status == "EXITING" and held.option_exit_qty == 0
    assert held.protection_state == "ACTIVE" and held.gtt_id in env.broker.gtts   # still protected


def test_a_fired_stop_whose_sell_is_resting_is_cancelled_and_sold_at_market_in_one_flatten(env):
    pos = _open(env)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 20.0, 21.0)
    child = env.broker.fire_gtt_unfilled(pos.gtt_id, status="TRIGGER PENDING")
    before = len(env.broker.placed)
    plain_delete = env.broker.delete_gtt

    async def delete_like_kite(trigger_id):
        # Kite refuses to delete a trigger that has already fired.
        if env.broker.gtts[int(trigger_id)]["status"] == "triggered":
            raise RuntimeError("the trigger has already been triggered")
        return await plain_delete(trigger_id)

    env.broker.delete_gtt = delete_like_kite

    result = _flatten(env)

    assert result["closed"] == 1 and result["failed"] == []
    assert child in env.broker.cancelled_orders                      # no 30s wait on the grace period
    sells = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[before:]]
    assert sells == [(OPTION_SYMBOL, "SELL", 75), (FUTURE_SYMBOL, "SELL", 75)]
    assert _get(env, pos).status == "CLOSED"


def test_a_stop_that_fired_and_filled_only_needs_the_hedge_closed(env):
    pos = _open(env)
    env.broker.fire_gtt(pos.gtt_id, 194.0)
    before = len(env.broker.placed)

    result = _flatten(env)

    assert result["closed"] == 1
    assert [(o["symbol"], o["side"]) for o in env.broker.placed[before:]] == [(FUTURE_SYMBOL, "SELL")]


def test_positions_are_left_alone_and_reported_when_the_exchange_is_closed(env):
    pos = _open(env)
    env.clock = env.clock.replace(hour=20, minute=0)
    before = len(env.broker.placed)

    result = _flatten(env)

    assert result["closed"] == 0 and result["failed"] == []
    assert len(result["skipped"]) == 1 and "market_closed" in result["skipped"][0]
    held = _get(env, pos)
    assert held.status == "OPEN" and held.protection_state == "ACTIVE"   # the broker stop stands
    assert len(env.broker.placed) == before


def test_another_accounts_position_is_not_touched(env):
    pos = _open(env)

    def elsewhere(p):
        p.account_id = "SOMEONE-ELSE"

    env.positions.mutate(pos.position_id, elsewhere)

    result = _flatten(env)

    assert result["closed"] == 0 and "other_account" in result["skipped"][0]
    assert _get(env, pos).status == "OPEN"


def test_a_flatten_waits_for_the_running_cycle_and_reports_busy_rather_than_racing_it(env):
    from app.services import snapback_dispatcher

    pos = _open(env)
    env.monkeypatch.setattr(snapback_dispatcher, "FLATTEN_LOCK_WAIT_S", 0.05)

    async def scenario():
        async with env.dispatcher._lock(env.uid):          # a cycle is mid-flight
            return await env.dispatcher.flatten_all(env.uid, env.broker)

    result = run(scenario())

    assert result["closed"] == 0 and "dispatcher_busy" in result["failed"][0]
    assert _get(env, pos).status == "OPEN"


def test_a_plan_armed_but_never_sent_is_cancelled_so_it_cannot_enter_after_the_flatten(env):
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    published = run(env.dispatcher.publish_from_signal(env.uid, row["signal_id"]))
    assert published["ok"], published
    env.plan_store.arm(published["plan_id"], expected_revision=published["revision"],
                       idempotency_key="k-flatten", armed_by=env.uid, mode="LIVE", now=SWING_NOW)

    result = _flatten(env)

    assert result["cancelled_plans"] == [published["plan_id"]]
    assert env.plan_store.get(published["plan_id"])["status"] == "CANCELLED"
    assert env.broker.placed == []
    assert env.positions.open_positions(env.uid) == []


def test_an_entry_that_may_exist_at_the_broker_is_reported_not_guessed_at(env):
    env.broker.mode = "unknown"                              # the option BUY's outcome is unknown
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    plans = env.plan_store.list_in_flight(owner_uid=env.uid)
    assert len(plans) == 1

    result = _flatten(env)

    assert result["closed"] == 0
    assert len(result["skipped"]) == 1 and "entry_may_exist_at_broker" in result["skipped"][0]
    assert result["cancelled_plans"] == []
    assert env.plan_store.get(plans[0]["plan_id"])["status"] != "CANCELLED"

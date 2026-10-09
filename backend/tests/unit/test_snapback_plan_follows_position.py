"""A plan whose position has closed (or begun to exit) must never act again.

A swing entry whose futures hedge ended with nothing filled stays RECONCILING with a live
ENTERING position, and the dispatcher retries the hedge every cycle. If the operator
squares off (or any exit closes the option) before the hedge goes out, nothing used to retire
that plan: the next recovery pass bought the futures hedge for a position that no longer
existed, leaving a naked long future the registry could not see.
"""
from __future__ import annotations

from tests.snapback_live_harness import FUTURE_SYMBOL, live_env, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env


def _hedge_orders(env):
    return [o for o in env.broker.placed if o["symbol"] == FUTURE_SYMBOL]


def _kill_switch_on() -> bool:
    from app.services import live_safety

    return bool(live_safety.kill_switch_state().get("enabled"))


def _flatten(env, reason="operator"):
    from app.services import snapback_dispatcher, snapback_live_exits

    env.monkeypatch.setattr(snapback_dispatcher, "_dispatcher", env.dispatcher)
    return run(snapback_live_exits.flatten_all(env.uid, env.broker, reason=reason))


def _entering_with_unhedged_option(env):
    """Option bought and stopped, hedge zero-filled: plan RECONCILING, position ENTERING."""
    env.broker.hedge_mode = "hang"
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (plan,) = env.plan_store.list_in_flight(owner_uid=env.uid)
    assert plan["status"] == "RECONCILING"
    (pos,) = env.positions.open_positions(env.uid)
    assert pos.status == "ENTERING" and not pos.has_hedge
    return plan["plan_id"], pos


def test_flattening_an_entering_position_retires_the_plan_that_would_hedge_it(env):
    plan_id, pos = _entering_with_unhedged_option(env)
    hedges_before = len(_hedge_orders(env))

    result = _flatten(env)

    assert result["closed"] == 1, result
    assert env.positions.get(pos.position_id).status == "CLOSED"
    assert env.plan_store.get(plan_id)["status"] == "CANCELLED"
    assert env.plan_store.list_in_flight(owner_uid=env.uid) == []

    env.broker.hedge_mode = "fill"                   # the book is fillable again
    _cycle(env)
    _cycle(env)

    assert len(_hedge_orders(env)) == hedges_before, "a closed position must not be hedged"
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0
    assert not _kill_switch_on()


def test_a_normal_exit_that_closes_an_entering_position_retires_its_plan_too(env):
    plan_id, pos = _entering_with_unhedged_option(env)
    hedges_before = len(_hedge_orders(env))

    outcome = run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "TEST"))

    assert outcome == "closed"
    env.broker.hedge_mode = "fill"
    _cycle(env)

    assert len(_hedge_orders(env)) == hedges_before
    assert env.plan_store.get(plan_id)["status"] == "CANCELLED"
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def test_the_executor_itself_refuses_to_hedge_a_position_that_is_closed_or_exiting(env):
    plan_id, pos = _entering_with_unhedged_option(env)
    hedges_before = len(_hedge_orders(env))
    for status in ("EXITING", "CLOSED"):
        def to(p, s=status):
            p.status = s

        env.positions.mutate(pos.position_id, to)
        env.broker.hedge_mode = "fill"

        result = run(env.dispatcher.execute_plan(env.uid, plan_id))

        assert result.status == "REFUSED", (status, result)
        assert result.reasons == [f"position_{status.lower()}"], (status, result)
        assert not result.stop_new_entries
        assert len(_hedge_orders(env)) == hedges_before, status


def test_a_plan_is_held_not_hedged_while_its_position_needs_reconciliation(env):
    plan_id, pos = _entering_with_unhedged_option(env)
    hedges_before = len(_hedge_orders(env))

    def flag(p):
        p.status = "RECONCILING"

    env.positions.mutate(pos.position_id, flag)
    env.broker.hedge_mode = "fill"
    _cycle(env)

    assert len(_hedge_orders(env)) == hedges_before
    assert env.plan_store.get(plan_id)["status"] == "RECONCILING"   # held for a person, not guessed at


def test_a_plan_with_a_hedge_order_that_may_still_be_live_is_held_not_retired(env):
    env.broker.hedge_mode = "unknown"                # the hedge BUY's outcome is not known
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (plan,) = env.plan_store.list_in_flight(owner_uid=env.uid)
    assert plan["status"] == "RECONCILING"
    hedges_before = len(_hedge_orders(env))

    result = _flatten(env)

    assert result["closed"] == 1 and result["cancelled_plans"] == [], result
    assert env.plan_store.get(plan["plan_id"])["status"] == "RECONCILING"   # still watched
    env.broker.hedge_mode = "fill"
    _cycle(env)
    assert len(_hedge_orders(env)) == hedges_before, "an unknown hedge is never re-sent"


def test_cancel_plan_retires_a_plan_whose_position_has_closed(env):
    plan_id, pos = _entering_with_unhedged_option(env)
    run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "TEST"))

    refused = run(env.dispatcher.cancel_plan(env.uid, plan_id))

    assert refused["ok"] is True
    assert env.plan_store.get(plan_id)["status"] == "CANCELLED"

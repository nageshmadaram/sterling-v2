"""A zero-fill entry is a finished plan, not a wedged one.

An exchange REJECT, or a marketable LIMIT that sat unfilled until the fill wait ran
out and was cancelled, leaves nothing at the broker. The plan used to stay ARMED; the
next dispatcher cycle then saw a journal key, flagged the plan RECONCILING, found
canonical rejected/cancelled with no fill, flagged it RECONCILING again and engaged
the GLOBAL kill switch, which blocks every other strategy as well, every cycle.
"""
from __future__ import annotations

import pytest

from tests.snapback_live_harness import (
    FUTURE_SYMBOL, OPTION_SYMBOL, LiveBroker, live_env, swing_row,
)
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env


def _kill_switch() -> bool:
    from app.services import live_safety

    return bool(live_safety.kill_switch_state()["enabled"])


def _plan_for(env, signal):
    from app.services.snapback_plan_producer import plan_id_for

    return env.plan_store.get(plan_id_for(signal))


@pytest.mark.parametrize("mode", ["reject", "hang"])
def test_a_zero_fill_entry_cancels_the_plan_and_never_trips_the_kill_switch(env, mode):
    env.broker.mode = mode
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]

    for _ in range(4):
        _cycle(env)

    plan = _plan_for(env, row["signal_id"])
    assert plan["status"] == "CANCELLED"
    assert plan["cancel_reason"] == "entry_not_filled"
    assert env.plan_store.list_in_flight(owner_uid=env.uid) == []
    assert not _kill_switch()
    assert env.positions.open_positions(env.uid) == []
    # one option order, never resent, no hedge, no stop
    assert [(o["symbol"], o["side"]) for o in env.broker.placed] == [(OPTION_SYMBOL, "BUY")]
    assert env.broker.gtts == {}


def test_a_cancelled_zero_fill_plan_is_never_retried_for_the_same_signal(env):
    env.broker.mode = "reject"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)

    env.broker.mode = "fill"             # the exchange would take it now ...
    again = _cycle(env)

    assert again["refusals"][row["signal_id"]].startswith("plan_exists:CANCELLED")
    assert len(env.broker.placed) == 1   # ... but one signal is one authorisation, forever


def test_a_restart_with_the_plan_still_armed_and_a_rejected_journal_retires_it(env):
    """The old state a deployed database may already hold: ARMED + terminal rejected key."""
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    real = SnapbackLiveExecutor._retire_unfilled_entry
    env.monkeypatch.setattr(SnapbackLiveExecutor, "_retire_unfilled_entry",
                            lambda self, *a, **k: None)
    env.broker.mode = "reject"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)
    assert _plan_for(env, row["signal_id"])["status"] == "ARMED"
    env.monkeypatch.setattr(SnapbackLiveExecutor, "_retire_unfilled_entry", real)

    env.dispatcher = env.build_dispatcher()      # a new process
    _cycle(env)
    _cycle(env)

    assert _plan_for(env, row["signal_id"])["status"] == "CANCELLED"
    assert not _kill_switch()
    assert len(env.broker.placed) == 1


def test_a_reconciling_plan_whose_entry_is_proven_unfilled_is_cancelled_without_a_halt(env):
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    real = SnapbackLiveExecutor._retire_unfilled_entry
    env.monkeypatch.setattr(SnapbackLiveExecutor, "_retire_unfilled_entry",
                            lambda self, *a, **k: None)
    env.broker.mode = "hang"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)
    plan = _plan_for(env, row["signal_id"])
    env.plan_store.mark_reconciling(plan["plan_id"], reason="restart_after_submission")
    env.monkeypatch.setattr(SnapbackLiveExecutor, "_retire_unfilled_entry", real)

    result = run(env.dispatcher.execute_plan(env.uid, plan["plan_id"]))

    assert result.status == "REJECTED" and result.reasons == ["entry_not_filled"]
    assert result.stop_new_entries is False
    assert env.plan_store.get(plan["plan_id"])["status"] == "CANCELLED"
    assert not _kill_switch()


def test_an_unknown_outcome_is_still_reconciling_and_still_halts(env):
    """The fix must not weaken UNKNOWN => recovery: no proof of no fill, no cancel."""
    env.broker.mode = "unknown"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]

    _cycle(env)
    _cycle(env)

    assert _plan_for(env, row["signal_id"])["status"] == "RECONCILING"
    assert env.broker.place_calls == 1


# ------------------------------------------------------ the operator cancel route


def _cancel_client(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import snapback_plans
    from app.services import snapback_dispatcher

    monkeypatch.setattr(snapback_plans, "_store", lambda: env.plan_store)
    monkeypatch.setattr(snapback_dispatcher, "get_dispatcher", lambda: env.dispatcher)
    app = FastAPI()
    app.include_router(snapback_plans.router)
    return TestClient(app)


def _publish_and_arm(env, signal):
    from app.services.snapback_plan_producer import PlanContext, build_plan, plan_id_for

    async def go():
        ctx = PlanContext(uid=env.uid, account_id=env.broker._account_id, client=env.broker,
                          cfg=env.cfg, now=env.clock, available_capital=1_000_000.0,
                          instruments=lambda ex: env.broker.search_instruments("", ex),
                          runtime_sha="t", manual=True)
        return await build_plan(swing_row(signal_id=signal), ctx)

    outcome = run(go())
    assert outcome.ok, outcome
    published = env.plan_store.publish(**outcome.plan)
    return plan_id_for(signal), published


def test_the_operator_can_cancel_a_published_plan_that_was_never_sent(env, monkeypatch):
    client = _cancel_client(env, monkeypatch)
    plan_id, _ = _publish_and_arm(env, _signal_id())

    response = client.post(f"/snapback/plans/{plan_id}/cancel", headers={"X-User-Id": env.uid})

    assert response.status_code == 200, response.text
    assert env.plan_store.get(plan_id)["status"] == "CANCELLED"
    assert env.broker.placed == []
    again = client.post(f"/snapback/plans/{plan_id}/cancel", headers={"X-User-Id": env.uid})
    assert again.status_code == 200 and again.json()["idempotent"] is True


def test_a_plan_whose_entry_may_be_live_cannot_be_cancelled_by_a_click(env, monkeypatch):
    env.broker.mode = "unknown"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)
    plan = _plan_for(env, row["signal_id"])
    assert plan["status"] == "RECONCILING"
    client = _cancel_client(env, monkeypatch)

    response = client.post(f"/snapback/plans/{plan['plan_id']}/cancel",
                           headers={"X-User-Id": env.uid})

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "entry_may_exist_at_broker"
    assert env.plan_store.get(plan["plan_id"])["status"] == "RECONCILING"


def test_a_plan_that_became_a_position_cannot_be_cancelled(env, monkeypatch):
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)
    plan = _plan_for(env, row["signal_id"])
    assert plan["status"] == "CONSUMED"
    client = _cancel_client(env, monkeypatch)

    response = client.post(f"/snapback/plans/{plan['plan_id']}/cancel",
                           headers={"X-User-Id": env.uid})

    assert response.status_code == 409
    assert env.plan_store.get(plan["plan_id"])["status"] == "CONSUMED"
    assert len(env.positions.open_positions(env.uid)) == 1


def test_the_store_refuses_to_cancel_a_consumed_plan(env):
    from app.services.snapback_plan_store import PlanConflictError

    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)
    plan = _plan_for(env, row["signal_id"])

    with pytest.raises(PlanConflictError):
        env.plan_store.cancel(plan["plan_id"], reason="oops")
    assert env.plan_store.get(plan["plan_id"])["status"] == "CONSUMED"

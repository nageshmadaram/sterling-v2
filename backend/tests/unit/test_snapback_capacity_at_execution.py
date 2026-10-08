"""Capacity and hedge netting are enforced when a plan EXECUTES, not only when it is built.

The manual path publishes a plan, and arms and executes it later. Two plans published
before either ran both passed the build-time capacity check, were both armed, and both
executed: two positions on one underlying and one option contract, with
``one_position_per_underlying`` on. Execution now re-checks against what is open and
what is ahead of the plan in the queue, and the build-time check counts armed plans
that have not produced a position yet.
"""
from __future__ import annotations

from tests.snapback_live_harness import (
    FUTURE_SYMBOL, OPTION_SYMBOL, SWING_NOW, live_env, swing_row,
)
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env

BANK_PUT = "BANKNIFTY26NOV25000PE"


def _publish(env, row):
    env.rows = [*[r for r in env.rows if r["signal_id"] != row["signal_id"]], row]
    published = run(env.dispatcher.publish_from_signal(env.uid, row["signal_id"]))
    assert published["ok"], published
    return published


def _arm(env, published, *, key=None):
    return env.plan_store.arm(
        published["plan_id"], expected_revision=published["revision"],
        idempotency_key=key or f"k-{published['plan_id']}", armed_by=env.uid, mode="LIVE",
        now=SWING_NOW)


def _bank_put_row(signal: str):
    return swing_row(signal_id=signal, symbol="BANKNIFTY", option_symbol=BANK_PUT,
                     cash_tradingsymbol="NIFTY BANK", lot_size=75)


def _quote_bank(env):
    env.broker.set_quote(f"NFO:{BANK_PUT}", 299.0, 300.0)
    env.broker.set_quote("NSE:NIFTY BANK", 25000.0, 25000.0, 25000.0)


def test_two_plans_for_one_underlying_published_before_either_runs_cannot_both_execute(env):
    first = _publish(env, swing_row(signal_id=_signal_id()))
    second = _publish(env, swing_row(signal_id=_signal_id()))
    _arm(env, first)
    _arm(env, second)

    a = run(env.dispatcher.execute_plan(env.uid, first["plan_id"]))
    placed = len(env.broker.placed)
    b = run(env.dispatcher.execute_plan(env.uid, second["plan_id"]))

    assert a.status == "OPEN"
    assert b.status == "REFUSED" and b.reasons == ["underlying_already_open"]
    assert len(env.broker.placed) == placed                   # no second option, hedge or stop
    assert len(env.positions.open_positions(env.uid)) == 1


def test_the_open_position_cap_is_enforced_at_execution(env):
    from dataclasses import replace

    _quote_bank(env)
    env.monkeypatch.setattr(
        "app.services.snapback.get_config", lambda u=None: replace(env.cfg, max_open_positions=1))
    first = _publish(env, swing_row(signal_id=_signal_id()))
    second = _publish(env, _bank_put_row(_signal_id("snapback:fade_up:BANKNIFTY")))
    _arm(env, first)
    _arm(env, second)

    a = run(env.dispatcher.execute_plan(env.uid, first["plan_id"]))
    b = run(env.dispatcher.execute_plan(env.uid, second["plan_id"]))

    assert a.status == "OPEN"
    assert b.status == "REFUSED" and b.reasons == ["max_open_positions"]
    assert len(env.positions.open_positions(env.uid)) == 1


def test_an_armed_plan_holds_its_place_against_a_plan_built_after_it(env):
    first = _publish(env, swing_row(signal_id=_signal_id()))
    _arm(env, first)                                          # armed, not yet executed
    env.rows = [swing_row(signal_id=_signal_id())]
    second_signal = env.rows[0]["signal_id"]

    result = run(env.dispatcher.publish_from_signal(env.uid, second_signal))

    assert result["ok"] is False and "underlying_already_open" in result["reasons"]


def test_two_queued_plans_do_not_refuse_each_other(env):
    """Both armed, neither run: the one armed first runs, the later one is the one refused."""
    first = _publish(env, swing_row(signal_id=_signal_id()))
    second = _publish(env, swing_row(signal_id=_signal_id()))
    _arm(env, first)
    _arm(env, second)

    # the LATER plan is asked first: it must wait behind the earlier one, not win the race
    later = run(env.dispatcher.execute_plan(env.uid, second["plan_id"]))
    assert later.status == "REFUSED" and later.reasons == ["underlying_already_open"]
    assert env.positions.open_positions(env.uid) == []

    earlier = run(env.dispatcher.execute_plan(env.uid, first["plan_id"]))
    assert earlier.status == "OPEN"


def test_a_plan_in_recovery_holds_its_underlying_even_with_no_position_row(env):
    first = _publish(env, swing_row(signal_id=_signal_id()))
    second = _publish(env, swing_row(signal_id=_signal_id()))
    _arm(env, first)
    env.broker.mode = "unknown"                               # the option BUY's outcome is unknown
    run(env.dispatcher.execute_plan(env.uid, first["plan_id"]))
    assert [p["status"] for p in env.plan_store.list_in_flight(owner_uid=env.uid)] == ["RECONCILING"]
    assert env.positions.open_positions(env.uid) == []        # nothing registered, yet it may exist
    env.broker.mode = "fill"
    _arm(env, second)
    placed = len(env.broker.placed)

    result = run(env.dispatcher.execute_plan(env.uid, second["plan_id"]))

    assert result.status == "REFUSED" and result.reasons == ["underlying_already_open"]
    assert len(env.broker.placed) == placed


def _arm_client(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints import snapback_plans

    monkeypatch.setattr(snapback_plans, "_store", lambda: env.plan_store)
    monkeypatch.setattr(snapback_plans, "_admission", lambda **kw: {"allowed": True, "reasons": []})
    monkeypatch.setattr(snapback_plans, "_schedule_execution", lambda uid, plan_id: "scheduled")
    original_arm = env.plan_store.arm          # the plans are built on the harness clock
    monkeypatch.setattr(env.plan_store, "arm",
                        lambda *a, **k: original_arm(*a, **{"now": SWING_NOW, **k}))
    app = FastAPI()
    app.include_router(snapback_plans.router)
    return TestClient(app)


def test_arming_a_plan_the_book_has_since_filled_is_refused_with_the_reason(env, monkeypatch):
    client = _arm_client(env, monkeypatch)
    first = _publish(env, swing_row(signal_id=_signal_id()))
    second = _publish(env, swing_row(signal_id=_signal_id()))
    headers = {"X-User-Id": env.uid}

    ok = client.post(f"/snapback/plans/{first['plan_id']}/arm", headers=headers,
                     json={"expected_revision": first["revision"], "idempotency_key": "a1"})
    assert ok.status_code == 200, ok.text

    refused = client.post(f"/snapback/plans/{second['plan_id']}/arm", headers=headers,
                          json={"expected_revision": second["revision"], "idempotency_key": "a2"})

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["error"] == "plan_conflicts_with_book"
    assert detail["reasons"] == ["underlying_already_open"]
    assert env.plan_store.get(second["plan_id"])["status"] == "PUBLISHED"     # nothing was armed

    replay = client.post(f"/snapback/plans/{first['plan_id']}/arm", headers=headers,
                         json={"expected_revision": first["revision"], "idempotency_key": "a1"})
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True

"""The lost-reply fence on Snapback's broker stop, as Supertrend and Gamma already have it.

A GTT placement is a POST whose answer can be lost (a read timeout, a 5xx) after Kite created the
trigger. Snapback used to read that as "nothing placed", recorded the stop FAILED with no id,
and the next attempt (the executor's second call, or the exit manager's tick) placed a second
trigger: two SELL triggers over one position, either of which could fire and sell twice, and a
stray one left resting after the position closed. The placement is now fenced durably before the
POST, an unknown outcome is resolved against the broker's trigger book before anything is sent
again, and a flatten removes a trigger the position never recorded.
"""
from __future__ import annotations

import pytest

from tests.snapback_live_harness import FUTURE_SYMBOL, OPTION_SYMBOL, live_env, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env

OPT_NET = ("NFO", "NRML", OPTION_SYMBOL)


def _active(env):
    return {g: v for g, v in env.broker.gtts.items() if v["status"] == "active"}


def _kill_switch_on() -> bool:
    from app.services import live_safety

    return bool(live_safety.kill_switch_state().get("enabled"))


def _flatten(env, reason="operator"):
    from app.services import snapback_dispatcher, snapback_live_exits

    env.monkeypatch.setattr(snapback_dispatcher, "_dispatcher", env.dispatcher)
    return run(snapback_live_exits.flatten_all(env.uid, env.broker, reason=reason))


def _ensure(env, pos, quantity=75):
    manager = env.dispatcher.exit_manager
    return run(manager.protection.ensure(env.broker, env.positions.get(pos.position_id), quantity))


def _entering_without_a_stop(env):
    """An option bought and held, its stop placement lost: the position as the fence sees it."""
    env.broker.gtt_refused = True                    # the broker answers 400: nothing was placed
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    env.broker.gtt_refused = False
    return env.positions.open_positions(env.uid)[0]


# ------------------------------------------------------------------ placement


def test_a_stop_whose_reply_was_lost_is_adopted_not_placed_a_second_time(env):
    env.broker.gtt_reply_lost = 1                    # the trigger is created, the reply never read
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    assert len(env.broker.gtts) == 1, f"two stops would sell twice: {sorted(env.broker.gtts)}"
    (pos,) = env.positions.open_positions(env.uid)
    assert pos.gtt_id == next(iter(env.broker.gtts)) and pos.protection_state == "ACTIVE"
    assert pos.gtt_pending is False
    assert pos.status == "OPEN"
    assert not _kill_switch_on()
    assert env.broker.place_gtt_calls == 1


def test_pending_snapback_stop_never_adopts_gtt_owned_by_another_strategy(env):
    from app.services import external_positions

    env.broker.gtt_reply_lost = 1
    env.monkeypatch.setattr(
        external_positions,
        "owned_gtt_ids",
        lambda _account_id: frozenset(str(gid) for gid in env.broker.gtts),
    )
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    (pos,) = env.positions.open_positions(env.uid)
    foreign_ids = set(env.broker.gtts)
    assert foreign_ids
    assert pos.gtt_id == 0
    assert pos.gtt_pending is True
    assert pos.protection_state != "ACTIVE"
    assert env.broker.deleted_gtts.isdisjoint(foreign_ids)
    assert env.broker.place_gtt_calls == 1


def test_the_fence_is_latched_durably_before_the_post_leaves(env):
    seen = {}
    original = env.broker.place_gtt

    async def spy(**kw):
        (pos,) = env.positions.open_positions(env.uid)
        seen["pending"] = (pos.gtt_pending, pos.gtt_pending_qty, pos.gtt_pending_trigger)
        return await original(**kw)

    env.broker.place_gtt = spy
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    assert seen["pending"][0] is True and seen["pending"][1] == 75 and seen["pending"][2] > 0
    (pos,) = env.positions.open_positions(env.uid)
    assert pos.gtt_pending is False and pos.protection_state == "ACTIVE"


def test_an_empty_trigger_book_is_not_proof_inside_the_in_flight_window(env):
    from app.services import snapback_live_wiring

    env.broker.gtt_reply_lost = 1
    env.broker.gtt_reply_loss_mode = "before_create"  # the POST died; nothing was created
    env.monkeypatch.setattr(snapback_live_wiring, "GTT_IN_FLIGHT_GRACE_S", 3600.0)
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    assert env.broker.place_gtt_calls == 1, "no second POST while the first may still land"
    (pos,) = env.positions.open_positions(env.uid)
    assert pos.gtt_pending is True and pos.protection_state != "ACTIVE"
    assert _kill_switch_on(), "an unconfirmed stop must stop new entries"

    _ensure(env, pos)
    assert env.broker.place_gtt_calls == 1

    env.monkeypatch.setattr(snapback_live_wiring, "GTT_IN_FLIGHT_GRACE_S", 0.0)
    _ensure(env, pos)                                # the window has passed and the book is empty

    assert env.broker.place_gtt_calls == 2
    healed = env.positions.get(pos.position_id)
    assert healed.protection_state == "ACTIVE" and healed.gtt_pending is False
    assert len(_active(env)) == 1


def test_a_definitive_refusal_lifts_the_fence(env):
    pos = _entering_without_a_stop(env)

    held = env.positions.get(pos.position_id)
    assert held.gtt_id == 0
    assert held.gtt_pending is False, "a broker that said no created nothing: nothing to fence"


def test_an_unreadable_trigger_book_keeps_the_fence_and_places_nothing(env):
    env.broker.gtt_reply_lost = 1
    env.rows = [swing_row(signal_id=_signal_id())]
    env.broker.gtt_book_unreadable = True

    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    _ensure(env, pos)

    assert env.broker.place_gtt_calls == 1
    assert env.positions.get(pos.position_id).gtt_pending is True
    env.broker.gtt_book_unreadable = False
    _ensure(env, pos)
    assert env.broker.place_gtt_calls == 1           # adopted once the book can be read
    assert env.positions.get(pos.position_id).protection_state == "ACTIVE"


def test_two_identical_triggers_are_reduced_to_one(env):
    env.broker.gtt_reply_lost = 1
    env.rows = [swing_row(signal_id=_signal_id())]
    env.broker.gtt_book_unreadable = True            # the first reconcile cannot see the book
    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    # a duplicate of the same stop from an older double placement
    run(env.broker.place_gtt(**{k: v for k, v in next(iter(env.broker.gtts.values())).items()
                                if k != "status"}))
    assert len(env.broker.gtts) == 2
    env.broker.gtt_book_unreadable = False

    _ensure(env, pos)

    assert len(_active(env)) == 1, "one stop per position"
    kept = env.positions.get(pos.position_id)
    assert kept.gtt_id in _active(env) and kept.gtt_id == min(env.broker.gtts)


# ---------------------------------------------------------------------- exits


def _stray_stop(env, pos, *, pending=True):
    """The state a lost reply leaves: a resting trigger at the broker the position has no id for."""
    run(env.broker.place_gtt(
        trigger_type="single", tradingsymbol=OPTION_SYMBOL, exchange="NFO", last_price=299.0,
        trigger_values=[pos.desired_stop],
        orders=[{"exchange": "NFO", "tradingsymbol": OPTION_SYMBOL, "transaction_type": "SELL",
                 "quantity": 75, "order_type": "LIMIT", "price": pos.desired_stop * 0.98,
                 "product": "NRML"}]))
    gid = max(env.broker.gtts)

    def forget(p):
        p.gtt_id = 0
        p.gtt_qty = 0
        p.gtt_stop = 0.0
        p.protection_state = "FAILED"
        p.gtt_pending = pending
        p.gtt_pending_ms = 1
        p.gtt_pending_qty = 75
        p.gtt_pending_trigger = float(pos.desired_stop)

    env.positions.mutate(pos.position_id, forget)
    return gid


def test_a_flatten_removes_a_stop_the_position_never_recorded(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    run(env.broker.delete_gtt(pos.gtt_id))
    stray = _stray_stop(env, pos)
    env.broker.deleted_gtts.clear()

    result = _flatten(env)

    assert result["closed"] == 1 and result["failed"] == [], result
    assert stray in env.broker.deleted_gtts and _active(env) == {}
    assert env.broker.net.get(OPT_NET, 0) == 0


def test_a_stop_that_failed_with_no_id_is_searched_for_on_exit_even_without_a_fence(env):
    """The state the old code left: FAILED, gtt_id 0, no fence, a live trigger at the broker."""
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    run(env.broker.delete_gtt(pos.gtt_id))
    stray = _stray_stop(env, pos, pending=False)

    result = _flatten(env)

    assert result["closed"] == 1, result
    assert stray in env.broker.deleted_gtts and _active(env) == {}


def test_an_unreadable_trigger_book_blocks_a_rule_exit_but_not_an_operator_flatten(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    run(env.broker.delete_gtt(pos.gtt_id))
    _stray_stop(env, pos)
    env.broker.gtt_book_unreadable = True
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    sold_before = len(env.broker.placed)

    blocked = run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "PREMIUM_STOP"))

    assert blocked == "stop_not_released"
    assert len(env.broker.placed) == sold_before, "a stray trigger may rest: nothing is sold yet"

    result = _flatten(env)                           # the operator wants out regardless

    assert result["closed"] == 1, result
    assert env.broker.net.get(OPT_NET, 0) == 0


def test_a_sibling_positions_stop_is_never_taken_for_a_strays(env):
    from app.services.snapback_live_positions import LivePosition

    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    (pos,) = env.positions.open_positions(env.uid)
    run(env.broker.delete_gtt(pos.gtt_id))
    sibling_gid = _stray_stop(env, pos, pending=False)       # the sibling's own, recorded trigger
    twin = LivePosition(
        position_id="SBP-TWIN", uid=pos.uid, account_id=pos.account_id, signal_id="twin",
        lane="swing", underlying="NIFTY", side="fade_up", option_type="PE",
        option_exchange="NFO", option_symbol=OPTION_SYMBOL, option_qty=75, option_entry_price=300.0,
        status="OPEN", protection_state="ACTIVE", gtt_id=sibling_gid, gtt_qty=75,
        gtt_stop=float(pos.desired_stop), desired_stop=float(pos.desired_stop))
    env.positions.put(twin)
    env.positions.mutate(pos.position_id, lambda p: setattr(p, "gtt_id", 0))
    env.broker.net[OPT_NET] = 150

    result = run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "TEST"))

    assert sibling_gid not in env.broker.deleted_gtts, "that trigger belongs to the sibling position"
    assert env.positions.get("SBP-TWIN").gtt_id == sibling_gid
    assert result == "closed"

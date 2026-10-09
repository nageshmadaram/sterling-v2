"""A leg the broker's positions book does not show is not proof that it is gone.

Booking a shortfall as an unpriced exit cannot be undone: the units count as sold, the broker stop
is gone, the kill switch is engaged and the hedge is closed. One successful-but-empty (or partial)
positions reply used to be enough, and a position whose book read badly once ended RECONCILING
with its option still held, no stop over it and nothing managing it. The shortfall now has to
persist over several readings and a minute before anything irreversible happens; until then the
exit simply waits, the stop is put back and the position keeps being managed.
"""
from __future__ import annotations

import pytest

from tests.snapback_live_harness import FUTURE_SYMBOL, OPTION_SYMBOL, live_env, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env

OPT_KEY = f"NFO:{OPTION_SYMBOL}"
OPT_NET = ("NFO", "NRML", OPTION_SYMBOL)
FUT_NET = ("NFO", "NRML", FUTURE_SYMBOL)


def _open(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    return env.positions.open_positions(env.uid)[0]


def _tick(env):
    return run(env.dispatcher.exit_manager.tick(env.uid, env.broker))


def _get(env, pos):
    return env.positions.get(pos.position_id)


def _kill_switch_on() -> bool:
    from app.services import live_safety

    return bool(live_safety.kill_switch_state().get("enabled"))


def _no_clock_wait(env, confirmations=3):
    from app.services import snapback_live_exits

    env.monkeypatch.setattr(snapback_live_exits, "SHORTFALL_CONFIRMATIONS", confirmations)
    env.monkeypatch.setattr(snapback_live_exits, "SHORTFALL_CONFIRM_S", 0.0)


def test_one_empty_positions_reply_neither_books_the_option_nor_loses_its_stop(env):
    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 250.0, 251.0)       # well above the stop: an operator exit
    env.broker.positions_empty_reads = 2              # ...and the book reads empty on both looks
    before = len(env.broker.placed)

    outcome = run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "TEST"))

    held = _get(env, pos)
    assert outcome == "option_missing_unconfirmed"
    assert held.status == "EXITING" and held.option_exit_unknown_qty == 0 and held.option_exit_qty == 0
    assert "option_exit_price_unknown" not in held.flags
    assert held.protection_state == "ACTIVE" and held.gtt_id in env.broker.gtts, \
        "the stop released for the exit is put back while the shortfall is unconfirmed"
    assert len(env.broker.placed) == before, "nothing is sold that the book did not show"
    assert not _kill_switch_on()

    _tick(env)                                        # the book reads normally again

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_qty == 75
    assert closed.option_exit_unknown_qty == 0, "a read glitch must not leave 75 units 'unknown'"
    assert closed.option_exit_price == pytest.approx(250.0)
    assert env.broker.net.get(OPT_NET, 0) == 0 and env.broker.net.get(FUT_NET, 0) == 0
    assert not _kill_switch_on()


def test_a_missing_option_is_booked_only_after_the_shortfall_persists(env):
    _no_clock_wait(env, confirmations=3)
    pos = _open(env)
    env.broker.net[OPT_NET] = 0                       # really closed in the broker app
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)

    _tick(env)
    _tick(env)

    waiting = _get(env, pos)
    assert waiting.status == "EXITING" and waiting.option_exit_unknown_qty == 0
    assert waiting.shortfall_leg == "option" and waiting.shortfall_obs == 2
    assert not _kill_switch_on()

    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_unknown_qty == 75
    assert "option_exit_price_unknown" in closed.flags
    assert env.broker.net.get(FUT_NET, 0) == 0        # the hedge is closed once the loss is proven
    assert _kill_switch_on()


def test_the_shortfall_must_also_span_the_confirmation_time(env):
    from app.services import snapback_live_exits

    env.monkeypatch.setattr(snapback_live_exits, "SHORTFALL_CONFIRMATIONS", 1)
    env.monkeypatch.setattr(snapback_live_exits, "SHORTFALL_CONFIRM_S", 3600.0)
    pos = _open(env)
    env.broker.net[OPT_NET] = 0
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)

    for _ in range(4):
        _tick(env)

    assert _get(env, pos).status == "EXITING" and _get(env, pos).option_exit_unknown_qty == 0


def test_a_reading_that_finds_the_option_held_resets_the_shortfall(env):
    _no_clock_wait(env, confirmations=3)
    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    env.broker.positions_empty_reads = 2
    _tick(env)
    assert _get(env, pos).shortfall_obs == 1

    env.broker.positions_empty_reads = 0
    _tick(env)                                        # held after all: the exit sells it

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_unknown_qty == 0
    assert closed.shortfall_obs == 0 and closed.shortfall_leg == ""


def test_a_partly_held_option_is_sold_for_what_exists_before_the_rest_is_proven_gone(env):
    _no_clock_wait(env, confirmations=3)
    pos = _open(env)
    env.broker.net[OPT_NET] = 25
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    before = len(env.broker.placed)

    _tick(env)

    sells = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[before:]]
    assert sells == [(OPTION_SYMBOL, "SELL", 25)]
    half = _get(env, pos)
    assert half.option_exit_qty == 25 and half.option_exit_unknown_qty == 0   # 50 not booked yet

    _tick(env)
    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_unknown_qty == 50
    assert env.broker.net.get(FUT_NET, 0) == 0


def test_a_hedge_missing_on_one_reading_does_not_freeze_the_position(env):
    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    env.broker.net[FUT_NET] = 0                       # the hedge is not shown (a bad read, say)

    first = _tick(env)

    held = _get(env, pos)
    assert first["exits"][0]["outcome"] == "hedge_missing_unconfirmed"
    assert held.status == "EXITING", "RECONCILING is a stop to all automatic handling"
    assert not _kill_switch_on()

    env.broker.net[FUT_NET] = 75                      # the book is right again
    _tick(env)

    assert _get(env, pos).status == "CLOSED"
    assert env.broker.net.get(FUT_NET, 0) == 0


def test_a_hedge_that_stays_missing_is_held_for_a_person_with_the_option_stopped(env):
    _no_clock_wait(env, confirmations=3)
    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    env.broker.net[FUT_NET] = 0

    for _ in range(3):
        _tick(env)

    held = _get(env, pos)
    assert held.status == "RECONCILING" and "hedge_missing_at_broker" in held.flags
    assert _kill_switch_on()


def test_an_unexplained_vanished_option_after_a_fired_stop_waits_for_the_shortfall_too(env):
    _no_clock_wait(env, confirmations=2)
    pos = _open(env)
    gid = pos.gtt_id
    env.broker.gtts[gid]["status"] = "triggered"      # fired, but no order is visible for it
    env.broker.net[OPT_NET] = 0

    first = _tick(env)
    assert _get(env, pos).status == "EXITING" and _get(env, pos).option_exit_unknown_qty == 0
    assert first["exits"][0]["outcome"] == "option_missing_unconfirmed"

    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_unknown_qty == 75

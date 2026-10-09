"""Which sell is the broker stop's own, and when a position may be called closed.

The settlement of a fired broker stop used to treat every SELL on the option's contract that
was not the current exit order as the stop's. A position's own earlier partial MARKET exit
(its order id is cleared once booked) or another strategy's, or a manual, SELL on the same
contract was then booked a second time, the position closed with units still at the broker,
and nothing tracked them or held a stop over them.
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


def _open(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    return env.positions.open_positions(env.uid)[0]


def _tick(env):
    return run(env.dispatcher.exit_manager.tick(env.uid, env.broker))


def _get(env, pos):
    return env.positions.get(pos.position_id)


def _flatten(env, reason="operator"):
    from app.services import snapback_dispatcher, snapback_live_exits

    env.monkeypatch.setattr(snapback_dispatcher, "_dispatcher", env.dispatcher)
    return run(snapback_live_exits.flatten_all(env.uid, env.broker, reason=reason))


def _kite_refuses_to_delete_a_fired_trigger(env):
    plain = env.broker.delete_gtt

    async def delete_like_kite(trigger_id):
        if env.broker.gtts[int(trigger_id)]["status"] == "triggered":
            raise RuntimeError("the trigger has already been triggered")
        return await plain(trigger_id)

    env.broker.delete_gtt = delete_like_kite


def _sells_after(env, mark):
    return [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[mark:]]


def test_an_earlier_partial_exit_is_not_booked_again_when_the_re_armed_stop_fires(env):
    pos = _open(env)
    _kite_refuses_to_delete_a_fired_trigger(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    env.broker.mode = "partial"
    _tick(env)                                        # a MARKET exit sells 37 of 75 and books it
    half = _get(env, pos)
    assert half.status == "EXITING" and half.option_exit_qty == 37
    env.broker.mode = "fill"
    env.broker.set_quote(OPT_KEY, 250.0, 251.0)       # the market recovers: the remainder is re-stopped
    run(env.dispatcher.exit_manager.protection.ensure(env.broker, half, 38))
    gid = _get(env, pos).gtt_id
    assert gid and env.broker.net[OPT_NET] == 38
    env.broker.set_quote(OPT_KEY, 20.0, 21.0)         # then the re-armed stop fires and its LIMIT rests
    child = env.broker.fire_gtt_unfilled(gid, status="TRIGGER PENDING")
    before = len(env.broker.placed)

    result = _flatten(env)

    assert result["closed"] == 1 and result["failed"] == [], result
    assert child in env.broker.cancelled_orders
    assert _sells_after(env, before) == [(OPTION_SYMBOL, "SELL", 38), (FUTURE_SYMBOL, "SELL", 75)]
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_qty == 75
    assert closed.option_exit_value == pytest.approx(37 * 190.0 + 38 * 20.0)
    assert env.broker.net.get(OPT_NET, 0) == 0, "every option unit must really be sold"


def test_another_strategys_filled_sell_on_the_contract_is_not_this_positions_exit(env):
    pos = _open(env)
    _kite_refuses_to_delete_a_fired_trigger(env)
    foreign = {"order_id": "OTHER-1", "tradingsymbol": OPTION_SYMBOL, "exchange": "NFO",
               "transaction_type": "SELL", "product": "NRML", "quantity": 25,
               "filled_quantity": 25, "average_price": 200.0, "status": "COMPLETE", "tag": None}
    resting = {**foreign, "order_id": "OTHER-2", "quantity": 10, "filled_quantity": 0,
               "average_price": 0.0, "status": "OPEN"}
    env.broker.orders["OTHER-1"] = foreign
    env.broker.orders["OTHER-2"] = resting
    env.broker.set_quote(OPT_KEY, 20.0, 21.0)
    child = env.broker.fire_gtt_unfilled(pos.gtt_id, status="TRIGGER PENDING")
    before = len(env.broker.placed)

    result = _flatten(env)

    assert result["closed"] == 1 and result["failed"] == [], result
    assert child in env.broker.cancelled_orders
    assert "OTHER-2" not in env.broker.cancelled_orders, "someone else's resting order is not ours to cancel"
    assert _sells_after(env, before) == [(OPTION_SYMBOL, "SELL", 75), (FUTURE_SYMBOL, "SELL", 75)]
    closed = _get(env, pos)
    assert closed.option_exit_qty == 75 and closed.option_exit_value == pytest.approx(75 * 20.0)


def test_a_position_is_never_closed_while_the_broker_still_holds_the_option(env):
    from app.services import live_safety

    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)

    def believed_sold(p):
        # What a double-booked exit looks like: Sterling believes all 75 are gone.
        p.status = "EXITING"
        p.exit_reason = "PREMIUM_STOP"
        p.option_exit_qty = 75
        p.option_exit_value = 75 * 190.0
        p.gtt_id = 0
        p.gtt_qty = 0
        p.protection_state = "NONE"

    env.positions.mutate(pos.position_id, believed_sold)
    env.broker.gtts.clear()
    env.broker.net[OPT_NET] = 37                      # ...but the broker still holds 37

    manager = env.dispatcher.exit_manager
    first = run(manager.exit_position(env.broker, pos.position_id, "TEST"))
    assert first == "option_residual_unconfirmed"       # one reading is not enough to hold it
    assert _get(env, pos).status == "EXITING"
    run(manager.exit_position(env.broker, pos.position_id, "TEST"))

    held = _get(env, pos)
    assert held.status == "RECONCILING", held.status
    assert held.status != "CLOSED"
    assert env.broker.net[OPT_NET] == 37
    assert live_safety.kill_switch_state()["enabled"] is True
    assert any("option_still_held" in f for f in held.flags)


def test_a_sibling_positions_units_on_the_same_contract_do_not_block_the_close(env):
    pos = _open(env)
    env.broker.set_quote(OPT_KEY, 190.0, 191.0)
    # Another live Snapback position holds 75 of the same contract in the same account.
    from app.services.snapback_live_positions import LivePosition

    twin = LivePosition(
        position_id="SBP-TWIN", uid=pos.uid, account_id=pos.account_id, signal_id="twin",
        lane="swing", underlying="NIFTY", side="fade_up", option_type="PE",
        option_exchange="NFO", option_symbol=OPTION_SYMBOL, option_qty=75,
        option_entry_price=300.0, status="OPEN", protection_state="ACTIVE", gtt_id=9999,
        gtt_qty=75, gtt_stop=195.0, desired_stop=195.0, stop_premium=195.0)
    env.positions.put(twin)
    env.broker.net[OPT_NET] = 150
    env.broker.gtts[9999] = {"status": "active", "tradingsymbol": OPTION_SYMBOL, "exchange": "NFO",
                             "orders": [{"quantity": 75, "product": "NRML"}]}

    run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "TEST"))

    assert _get(env, pos).status == "CLOSED"
    assert env.broker.net[OPT_NET] == 75              # the twin's units are untouched

"""Every swing hedge is the same instrument (the NIFTY future), and Kite nets by
(symbol, product): two Snapback positions with OPPOSITE hedges would net to zero at
the broker. Each position's book then disagrees with the broker permanently, and
exiting one of them can never prove its hedge is there to close. Opposite hedges on
one futures contract are therefore refused, at publish, and again before any leg is
sent; and the book check compares the broker's net with the SUM of the signed hedges.
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

BANK_CALL = "BANKNIFTY26NOV25000CE"


def _bank_call_row(signal: str):
    """A fade_down on another index: a bought CALL, whose hedge is a SHORT NIFTY future."""
    return swing_row(signal_id=signal, symbol="BANKNIFTY", option_type="CE", side="fade_down",
                     delta=0.7, option_symbol=BANK_CALL, cash_tradingsymbol="NIFTY BANK",
                     lot_size=75)


def _quote_bank(env):
    env.broker.set_quote(f"NFO:{BANK_CALL}", 299.0, 300.0)
    env.broker.set_quote("NSE:NIFTY BANK", 25000.0, 25000.0, 25000.0)


def test_a_signal_whose_hedge_opposes_an_open_hedge_is_refused_at_plan_time(env):
    env.rows = [swing_row(signal_id=_signal_id())]            # NIFTY PE -> BUY the future
    _cycle(env)
    assert len(env.positions.open_positions(env.uid)) == 1
    placed = len(env.broker.placed)
    _quote_bank(env)
    other = _bank_call_row(_signal_id("snapback:fade_down:BANKNIFTY"))
    env.rows = [other]                                        # -> would SELL the same future

    status = _cycle(env)

    assert status["refusals"][other["signal_id"]] == "hedge_opposes_open_hedge"
    assert len(env.broker.placed) == placed                   # not one order
    assert env.plan_store.list_in_flight(owner_uid=env.uid) == []


def test_a_same_direction_hedge_on_the_same_future_is_still_allowed(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    _quote_bank(env)
    env.broker.set_quote(f"NFO:{BANK_CALL}", 299.0, 300.0)
    same = swing_row(signal_id=_signal_id("snapback:fade_up:BANKNIFTY"), symbol="BANKNIFTY",
                     option_symbol="BANKNIFTY26NOV25000PE", cash_tradingsymbol="NIFTY BANK",
                     lot_size=75)
    env.broker.set_quote("NFO:BANKNIFTY26NOV25000PE", 299.0, 300.0)
    env.rows = [same]

    status = _cycle(env)

    assert status["entries"] == 1, status
    assert len(env.positions.open_positions(env.uid)) == 2
    assert env.broker.net[("NFO", "NRML", FUTURE_SYMBOL)] == 150
    clean = run(_reconcile(env))
    assert clean.clean, clean.mismatches


def test_two_plans_published_before_either_runs_cannot_both_execute(env):
    """The manual path publishes then arms later: the second must be refused at execution."""
    _quote_bank(env)
    first = swing_row(signal_id=_signal_id())
    second = _bank_call_row(_signal_id("snapback:fade_down:BANKNIFTY"))
    env.rows = [first, second]
    one = run(env.dispatcher.publish_from_signal(env.uid, first["signal_id"]))
    two = run(env.dispatcher.publish_from_signal(env.uid, second["signal_id"]))
    assert one["ok"] and two["ok"], (one, two)
    for published in (one, two):
        env.plan_store.arm(published["plan_id"], expected_revision=published["revision"],
                           idempotency_key=f"k-{published['plan_id']}", armed_by=env.uid,
                           mode="LIVE", now=SWING_NOW)

    a = run(env.dispatcher.execute_plan(env.uid, one["plan_id"]))
    placed = len(env.broker.placed)
    b = run(env.dispatcher.execute_plan(env.uid, two["plan_id"]))

    assert a.status == "OPEN"
    assert b.status == "REFUSED" and b.reasons == ["hedge_opposes_open_hedge"]
    assert len(env.broker.placed) == placed                    # no option, no hedge, no stop
    assert len(env.positions.open_positions(env.uid)) == 1


async def _reconcile(env):
    from app.services.snapback_live_wiring import reconcile_book

    return await reconcile_book(env.broker, env.uid, env.broker._account_id, store=env.positions)


def _hedged_position(pid, symbol, hedge_side, qty=75):
    from app.services.snapback_live_positions import LivePosition

    return LivePosition(
        position_id=pid, uid="u", account_id="A", signal_id=pid, lane="swing",
        underlying="NIFTY", side="fade_up", option_type="PE", option_exchange="NFO",
        option_symbol=symbol, option_qty=75, option_entry_price=300.0, status="OPEN",
        hedge_exchange="NFO", hedge_symbol=FUTURE_SYMBOL, hedge_side=hedge_side,
        hedge_qty=qty, hedge_entry_price=25000.0, hedge_lot_size=75, product="NRML")


def _book(env, *positions, future_net):
    from app.services.snapback_live_positions import LivePositionStore

    store = LivePositionStore(env.tmp_path / f"book-{abs(hash(tuple(p.position_id for p in positions)))}.db")
    for pos in positions:
        pos.uid, pos.account_id = env.uid, env.broker._account_id
        store.put(pos)
        env.broker.net[("NFO", "NRML", pos.option_symbol)] = 75
    env.broker.net[("NFO", "NRML", FUTURE_SYMBOL)] = future_net
    return store


def test_the_book_compares_the_broker_net_with_the_sum_of_the_hedges(env):
    from app.services.snapback_live_wiring import reconcile_book

    store = _book(env, _hedged_position("A", "NIFTY26NOV25000PE", "BUY"),
                  _hedged_position("B", "BANKNIFTY26NOV25000PE", "BUY"), future_net=75)

    snap = run(reconcile_book(env.broker, env.uid, env.broker._account_id, store=store))

    # each position alone sees 75 >= 75, but two long hedges need 150 at the broker
    assert not snap.clean
    assert any(m.startswith("hedge_mismatch:NIFTY26NOVFUT:75/150") for m in snap.mismatches)


def test_two_agreeing_hedges_are_clean(env):
    from app.services.snapback_live_wiring import reconcile_book

    store = _book(env, _hedged_position("A", "NIFTY26NOV25000PE", "BUY"),
                  _hedged_position("B", "BANKNIFTY26NOV25000PE", "BUY"), future_net=150)

    snap = run(reconcile_book(env.broker, env.uid, env.broker._account_id, store=store))

    assert snap.clean, snap.mismatches


def test_hedges_that_cancel_at_the_broker_are_not_a_permanent_mismatch(env):
    """A legacy pair of opposite hedges nets to zero: that is what the book expects."""
    from app.services.snapback_live_wiring import reconcile_book

    store = _book(env, _hedged_position("A", "NIFTY26NOV25000PE", "BUY"),
                  _hedged_position("B", "BANKNIFTY26NOV25000CE", "SELL"), future_net=0)

    snap = run(reconcile_book(env.broker, env.uid, env.broker._account_id, store=store))

    assert snap.clean, snap.mismatches

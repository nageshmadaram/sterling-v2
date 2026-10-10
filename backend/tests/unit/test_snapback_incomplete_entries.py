"""An entry whose hedge has not filled is exposure, and it must count as one.

A zero-fill hedge is retried rather than halted, so the option stays bought and unhedged while the
dispatcher tries again. All Snapback hedges are the same NIFTY future: when one cannot fill, the
next will not either. The per-cycle origination budget was charged only for a fully OPEN entry, and
origination ignored plans still waiting on their hedge, so every cycle opened one more unhedged
option, up to the position cap, before the first hedge had filled.
"""
from __future__ import annotations

from tests.snapback_live_harness import FUTURE_SYMBOL, OPTION_SYMBOL, live_env, swing_row
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env

BANK_PUT = "BANKNIFTY26NOV25000PE"


def _bank_row():
    return swing_row(signal_id=_signal_id("snapback:fade_up:BANKNIFTY"), symbol="BANKNIFTY",
                     option_symbol=BANK_PUT, cash_tradingsymbol="NIFTY BANK", lot_size=75)


def _quote_bank(env):
    env.broker.set_quote(f"NFO:{BANK_PUT}", 299.0, 300.0)
    env.broker.set_quote("NSE:NIFTY BANK", 25000.0, 25000.0, 25000.0)


def _option_buys(env):
    return [o["symbol"] for o in env.broker.placed
            if o["side"] == "BUY" and not str(o["symbol"]).endswith("FUT")]


def test_two_armed_rows_do_not_both_enter_in_one_cycle_while_the_first_hedge_is_unfilled(env):
    _quote_bank(env)
    env.broker.hedge_mode = "hang"                      # no hedge will fill
    env.rows = [swing_row(signal_id=_signal_id()), _bank_row()]

    status = _cycle(env)

    assert len(_option_buys(env)) == 1, _option_buys(env)
    assert status["entries"] == 0, "the entry is not complete: its hedge is still being retried"
    assert [p["status"] for p in env.plan_store.list_in_flight(owner_uid=env.uid)] == ["RECONCILING"]


def test_no_new_entry_is_opened_while_a_plan_is_waiting_for_its_hedge(env):
    _quote_bank(env)
    env.broker.hedge_mode = "hang"
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    env.rows = [_bank_row()]

    status = _cycle(env)

    assert "hedge_incomplete" in status["blockers"], status
    assert _option_buys(env) == [OPTION_SYMBOL]          # nothing new, however many cycles pass
    _cycle(env)
    assert _option_buys(env) == [OPTION_SYMBOL]


def test_origination_resumes_once_the_hedge_has_filled(env):
    _quote_bank(env)
    env.broker.hedge_mode = "hang"
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    env.broker.hedge_mode = "fill"
    env.rows = [_bank_row()]

    _cycle(env)                                          # the retry completes the first position
    assert env.plan_store.list_in_flight(owner_uid=env.uid) == []
    status = _cycle(env)                                 # and the next signal may now enter

    assert "hedge_incomplete" not in status.get("blockers", [])
    assert _option_buys(env) == [OPTION_SYMBOL, BANK_PUT]
    assert env.broker.net[("NFO", "NRML", FUTURE_SYMBOL)] == 150


def test_the_blocker_is_visible_to_readiness_so_a_manual_arm_is_refused_too(env):
    from app.services import snapback_dispatcher

    env.broker.hedge_mode = "hang"
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)

    blockers = snapback_dispatcher.origination_blockers(
        env.uid, env.cfg, account_id=env.broker._account_id, auto=True,
        plan_store=env.plan_store, positions=env.positions)

    assert "hedge_incomplete" in blockers

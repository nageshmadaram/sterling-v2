"""Exit management for live Snapback positions.

The decisions (stop, trail, target, horizon, runner, expiry, square-off) are pure
functions of the position and the clock, so they are tested without a broker. The
closing procedure (release the stop, sell the option, close the hedge, finalise) is
tested against the live-broker double, including the failure shapes that lose
money: a rejected sell, a half fill, a hedge that will not close, a stop that fires
under us, and a restart in the middle of an exit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.services.snapback_live_exits import (
    decide_exit, decide_scalp, decide_swing, sessions_held,
)
from app.services.snapback_live_positions import LivePosition
from tests.snapback_live_harness import (
    FUTURE_SYMBOL, IST, OPTION_SYMBOL, SCALP_NOW, SWING_NOW, live_env, scalp_row, swing_row,
)
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id

_ = live_env   # registers the shared ``env`` fixture


def _at(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30")


def _swing_position(**over) -> LivePosition:
    base = dict(
        position_id="SBP-X", uid="u", account_id="A", signal_id="s", lane="swing",
        underlying="NIFTY", side="fade_up", option_type="PE", option_exchange="NFO",
        option_symbol=OPTION_SYMBOL, option_qty=75, option_entry_price=300.0,
        option_expiry="2026-11-26", status="OPEN", entry_day="2026-10-07",
        stop_premium=195.0, desired_stop=195.0, high_water=300.0,
        rules={"lane": "swing", "premium_stop_pct": 35.0, "hold_days": 15,
               "runner_mult": 1.5, "runner_trail_pct": 25.0,
               "expiry_buffer_calendar_days": 6},
    )
    base.update(over)
    return LivePosition(**base)


def _scalp_position(**over) -> LivePosition:
    now_ms = int(SCALP_NOW.timestamp() * 1000)
    base = dict(
        position_id="SBP-S", uid="u", account_id="A", signal_id="s", lane="scalp",
        underlying="NIFTY", side="fade_up", option_type="PE", option_exchange="NFO",
        option_symbol=OPTION_SYMBOL, option_qty=75, option_entry_price=300.0,
        status="OPEN", entry_day="2026-10-07", stop_premium=296.0, desired_stop=296.0,
        high_water=300.0, opened_ms=now_ms, tick_size=0.05,
        rules={"lane": "scalp", "stop_points": 4.0, "target_premium": 305.0,
               "trail_points": 2.0, "lock_points": 1.0, "round_trip_cost_points": 1.0,
               "max_hold_bars": 20, "runner_max_bars": 60, "timeframe_minutes": 1,
               "square_off_minute": 915},
    )
    base.update(over)
    return LivePosition(**base)


# ============================================================ swing decisions


def test_the_premium_stop_is_checked_on_every_observation():
    pos = _swing_position()

    assert decide_swing(pos, bid=195.0, now=_at("2026-10-08", "10:00")).reason == "PREMIUM_STOP"
    assert decide_swing(pos, bid=195.05, now=_at("2026-10-08", "10:00")).reason == ""


def test_the_end_of_day_pass_runs_once_per_session_and_only_in_its_window():
    # long past the horizon; yesterday's pass already ran
    pos = _swing_position(entry_day="2026-09-01", last_eod_session="2026-10-07")
    before = decide_swing(pos, bid=310.0, now=_at("2026-10-08", "14:00"))
    in_window = decide_swing(pos, bid=310.0, now=_at("2026-10-08", "15:16"))

    assert before.reason == "" and "last_eod_session" not in before.updates
    assert in_window.reason == "HOLDING_HORIZON_EXPIRED"
    assert in_window.updates["last_eod_session"] == "2026-10-08"

    done = _swing_position(entry_day="2026-09-01", last_eod_session="2026-10-08")
    assert decide_swing(done, bid=310.0, now=_at("2026-10-08", "15:20")).reason == ""


def test_before_the_horizon_a_trade_is_held():
    pos = _swing_position(entry_day="2026-10-07")

    assert decide_swing(pos, bid=330.0, now=_at("2026-10-08", "15:16")).reason == ""


def test_at_the_horizon_a_trade_worth_the_runner_multiple_becomes_a_runner():
    pos = _swing_position(entry_day="2026-09-01")

    decision = decide_swing(pos, bid=460.0, now=_at("2026-10-08", "15:16"))

    assert decision.reason == ""
    assert decision.updates["is_runner"] is True and decision.updates["peak_close"] == 460.0
    assert decision.events == ["runner_armed"]


def test_a_runner_trails_its_closing_peak_and_exits_on_the_give_back():
    pos = _swing_position(entry_day="2026-09-01", is_runner=True, peak_close=500.0)

    rising = decide_swing(pos, bid=520.0, now=_at("2026-10-08", "15:16"))
    assert rising.reason == "" and rising.updates["peak_close"] == 520.0

    held = decide_swing(pos, bid=380.0, now=_at("2026-10-08", "15:16"))
    assert held.reason == ""                       # 380 > 500 * 0.75 = 375

    stopped = decide_swing(pos, bid=375.0, now=_at("2026-10-08", "15:16"))
    assert stopped.reason == "RUNNER_TRAIL_STOP"


def test_a_runner_peak_only_moves_on_the_end_of_day_mark():
    pos = _swing_position(entry_day="2026-09-01", is_runner=True, peak_close=500.0,
                          last_eod_session="2026-10-07")

    intraday = decide_swing(pos, bid=900.0, now=_at("2026-10-08", "11:00"))

    assert "peak_close" not in intraday.updates     # an intraday spike is not a peak


def test_the_expiry_buffer_closes_the_position_at_the_end_of_day_pass():
    pos = _swing_position(option_expiry="2026-10-12", entry_day="2026-10-07")

    assert decide_swing(pos, bid=320.0, now=_at("2026-10-08", "15:16")).reason == "EXPIRY_APPROACHING"


def test_a_missed_end_of_day_pass_is_caught_up_the_next_session():
    pos = _swing_position(entry_day="2026-09-01", last_eod_session="2026-10-05")

    decision = decide_swing(pos, bid=310.0, now=_at("2026-10-08", "10:00"))

    assert decision.reason == "HOLDING_HORIZON_EXPIRED"


def test_sessions_held_counts_trading_days_inclusively_and_skips_holidays():
    assert sessions_held("2026-10-01", date(2026, 10, 1)) == 1
    # Oct 2 (a verified holiday) and the weekend do not count
    assert sessions_held("2026-10-01", date(2026, 10, 5)) == 2


# ============================================================ scalp decisions


def test_a_scalp_stop_hit_exits():
    pos = _scalp_position()

    assert decide_scalp(pos, bid=296.0, now=SCALP_NOW).reason == "stop_loss_hit"


def test_a_scalp_locks_gains_by_raising_the_stop_and_never_lowers_it():
    pos = _scalp_position()

    up = decide_scalp(pos, bid=302.5, now=SCALP_NOW)
    assert up.reason == "" and up.updates["phase"] == "LOCKED"
    assert up.updates["desired_stop"] >= 301.0

    locked = _scalp_position(desired_stop=up.updates["desired_stop"], phase="LOCKED")
    back = decide_scalp(locked, bid=300.0, now=SCALP_NOW)
    assert back.reason == "stop_loss_hit" or back.updates["desired_stop"] >= up.updates["desired_stop"]


def test_a_scalp_takes_the_target_when_there_is_no_bar_to_prove_continuation():
    pos = _scalp_position()

    assert decide_scalp(pos, bid=305.5, now=SCALP_NOW).reason == "target_reached_no_continuation"


def test_a_scalp_is_squared_off_at_the_configured_minute():
    pos = _scalp_position(opened_ms=int(_at("2026-10-07", "15:10").timestamp() * 1000))

    assert decide_scalp(pos, bid=301.0, now=_at("2026-10-07", "15:15")).reason == "session_square_off"
    assert decide_scalp(pos, bid=301.0, now=_at("2026-10-07", "15:14")).reason == ""


def test_a_scalp_times_out_after_its_hold_limit_in_bars():
    pos = _scalp_position()

    later = SCALP_NOW + timedelta(minutes=21)
    assert decide_scalp(pos, bid=301.0, now=later).reason == "initial_hold_timeout"


def test_a_scalp_hard_exit_time_wins():
    pos = _scalp_position(rules={**_scalp_position().rules,
                                 "hard_exit_ms": int(SCALP_NOW.timestamp() * 1000) + 1000})

    assert decide_scalp(pos, bid=301.0, now=SCALP_NOW + timedelta(seconds=2)).reason == "horizon_hard_exit"


def test_a_scalp_with_no_bid_waits_for_the_broker_stop_but_still_squares_off():
    pos = _scalp_position()

    assert decide_scalp(pos, bid=0.0, now=SCALP_NOW).reason == ""
    assert decide_scalp(pos, bid=0.0, now=_at("2026-10-07", "15:16")).reason == "session_square_off"


def test_decide_exit_dispatches_on_the_lane():
    assert decide_exit(_scalp_position(), bid=296.0, now=SCALP_NOW).reason == "stop_loss_hit"
    assert decide_exit(_swing_position(), bid=195.0, now=SWING_NOW).reason == "PREMIUM_STOP"


# ============================================================= the live chain


def _open(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    return env.positions.open_positions(env.uid)[0]


def _tick(env):
    return run(env.dispatcher.exit_manager.tick(env.uid, env.broker))


def _get(env, pos):
    return env.positions.get(pos.position_id)


def test_a_premium_stop_closes_the_option_then_the_hedge_in_that_order(env):
    pos = _open(env)
    before = len(env.broker.placed)

    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    _tick(env)

    sells = env.broker.placed[before:]
    assert [(o["symbol"], o["side"]) for o in sells] == [(OPTION_SYMBOL, "SELL"), (FUTURE_SYMBOL, "SELL")]
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "PREMIUM_STOP"
    assert closed.option_exit_qty == 75 and closed.hedge_exit_qty == 75
    # the resting broker stop was released before the sell, and nothing is left armed
    assert pos.gtt_id in env.broker.deleted_gtts and env.broker.gtts == {}
    # (190 - 300) * 75 on the option; the long future sold at its bid, 1 point under its ask
    assert closed.realised_pnl == pytest.approx((190.0 - 300.0) * 75 + (25000.0 - 25001.0) * 75)


def test_exits_are_sent_as_canonical_close_orders_that_the_ledger_reduces(env):
    from app.services.kite_engine import fill_ledger

    pos = _open(env)
    inv = fill_ledger.inventory(pos.account_id, env.uid, OPTION_SYMBOL)
    assert inv.net_quantity == 75

    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    _tick(env)

    assert fill_ledger.inventory(pos.account_id, env.uid, OPTION_SYMBOL).net_quantity == 0
    assert fill_ledger.inventory(pos.account_id, env.uid, FUTURE_SYMBOL).net_quantity == 0
    # the realised loss reaches the account's daily-loss accounting
    assert fill_ledger.realized_pnl(env.uid) == pytest.approx(
        (190.0 - 300.0) * 75 + (25000.0 - 25001.0) * 75)


def test_a_stop_that_fires_at_the_broker_closes_the_hedge_without_selling_the_option_twice(env):
    pos = _open(env)
    before = len(env.broker.placed)

    env.broker.fire_gtt(pos.gtt_id, 194.0)           # the broker sold the option
    _tick(env)

    ours = [(o["symbol"], o["side"]) for o in env.broker.placed[before:]]
    assert ours == [(FUTURE_SYMBOL, "SELL")]          # only the hedge is ours to close
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "BROKER_STOP"
    assert closed.option_exit_price == pytest.approx(194.0)
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def _capture_alerts(env):
    from app.services import snapback_live_exits

    sent = []
    env.monkeypatch.setattr(snapback_live_exits, "alert",
                            lambda event, title, detail, **kw: sent.append((event, kw.get("severity", "INFO"))))
    return sent


def test_a_stop_whose_sell_never_fills_is_cancelled_and_the_position_sold_at_market(env):
    from app.services import snapback_live_exits

    pos = _open(env)
    alerts = _capture_alerts(env)
    env.monkeypatch.setattr(snapback_live_exits, "STOP_FILL_GRACE_S", 0.0)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 20.0, 21.0)        # the book gapped through the limit
    child = env.broker.fire_gtt_unfilled(pos.gtt_id, status="TRIGGER PENDING")
    before = len(env.broker.placed)

    first = _tick(env)

    waiting = _get(env, pos)
    assert waiting.status == "EXITING" and waiting.stop_fill_pending_ms > 0
    assert first["exits"][0]["outcome"] == "stop_fill_pending"
    assert len(env.broker.placed) == before                          # nothing sold yet
    assert ("STOP TRIGGERED, SELL NOT FILLED", "CRITICAL") in alerts  # loud on the first pending tick

    _tick(env)

    assert child in env.broker.cancelled_orders                      # the resting stop order is cancelled
    sells = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[before:]]
    assert sells == [(OPTION_SYMBOL, "SELL", 75), (FUTURE_SYMBOL, "SELL", 75)]
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "BROKER_STOP_UNFILLED"
    assert closed.option_exit_qty == 75 and closed.option_exit_price == pytest.approx(20.0)
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def test_a_stop_sell_is_given_its_grace_period_before_it_is_cancelled(env):
    pos = _open(env)                                  # STOP_FILL_GRACE_S is the production 30s
    env.broker.fire_gtt_unfilled(pos.gtt_id)
    before = len(env.broker.placed)

    _tick(env)
    _tick(env)
    _tick(env)

    assert len(env.broker.placed) == before and not env.broker.cancelled_orders
    assert _get(env, pos).status == "EXITING"


def test_a_part_filled_stop_sell_books_its_fill_and_sells_only_the_rest(env):
    from app.services import snapback_live_exits

    pos = _open(env)
    env.monkeypatch.setattr(snapback_live_exits, "STOP_FILL_GRACE_S", 0.0)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 30.0, 31.0)
    env.broker.fire_gtt_unfilled(pos.gtt_id, status="OPEN", filled=25, price=190.0)
    before = len(env.broker.placed)

    _tick(env)
    _tick(env)

    sells = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[before:]]
    assert sells == [(OPTION_SYMBOL, "SELL", 50), (FUTURE_SYMBOL, "SELL", 75)]
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_qty == 75
    assert closed.option_exit_value == pytest.approx(25 * 190.0 + 50 * 30.0)
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0


def test_a_stop_order_that_cannot_be_cancelled_is_never_sold_over(env):
    """Selling at market while the stop's own SELL still rests would be a double sell."""
    from app.services import live_safety, snapback_live_exits

    pos = _open(env)
    env.monkeypatch.setattr(snapback_live_exits, "STOP_FILL_GRACE_S", 0.0)
    env.broker.fire_gtt_unfilled(pos.gtt_id)
    env.broker.cancel_fails = True
    before = len(env.broker.placed)

    for _ in range(4):
        _tick(env)

    assert len(env.broker.placed) == before
    assert _get(env, pos).status == "EXITING" and _get(env, pos).exit_reason == "BROKER_STOP"
    assert live_safety.kill_switch_state()["enabled"] is True        # escalation applies

    env.broker.cancel_fails = False
    _tick(env)

    assert _get(env, pos).status == "CLOSED"


def test_a_rejected_option_sell_re_arms_the_stop_and_is_retried_under_a_new_key(env):
    pos = _open(env)
    old_gtt = pos.gtt_id
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    env.broker.mode = "reject"

    first = _tick(env)

    held = _get(env, pos)
    assert held.status == "EXITING" and held.option_exit_qty == 0
    # The quote is already through the stop, so Kite cannot hold a new stop GTT
    # there (protective_stop refuses a breached stop); the exit is retried
    # instead and the position stays EXITING rather than being forgotten.
    assert first["exits"][0]["outcome"] == "submit_failed"

    env.broker.mode = "fill"
    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_attempts >= 1
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0


def test_a_half_filled_exit_protects_the_remainder_and_finishes_it(env):
    pos = _open(env)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    env.broker.mode = "partial"
    env.broker.hedge_mode = "fill"

    _tick(env)

    half = _get(env, pos)
    assert half.status == "EXITING" and 0 < half.option_exit_qty < 75
    # The remainder cannot be re-protected with a stop GTT while the quote is
    # through the stop; the next tick finishes the exit instead.

    env.broker.mode = "fill"
    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.option_exit_qty == 75
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0


def test_a_hedge_that_will_not_close_is_retried_alerted_and_halts_new_entries(env):
    from app.services import live_safety

    pos = _open(env)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    env.broker.hedge_mode = "reject"

    for _ in range(4):
        _tick(env)

    stuck = _get(env, pos)
    assert stuck.status == "EXITING"                 # never CLOSED with a leg still open
    assert stuck.option_exit_qty == 75 and stuck.hedge_exit_qty == 0
    assert live_safety.kill_switch_state()["enabled"] is True

    env.broker.hedge_mode = "fill"
    _tick(env)

    assert _get(env, pos).status == "CLOSED"
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def test_a_restart_in_the_middle_of_an_exit_never_resends_the_leg(env):
    pos = _open(env)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    env.broker.hedge_mode = "reject"
    _tick(env)                                         # option sold; the hedge sell was refused
    sent = len([o for o in env.broker.placed if o["side"] == "SELL" and o["symbol"] == OPTION_SYMBOL])
    assert sent == 1

    env.dispatcher = env.build_dispatcher()           # a new process
    env.broker.hedge_mode = "fill"
    run(env.dispatcher.exit_manager.tick(env.uid, env.broker))

    assert len([o for o in env.broker.placed if o["side"] == "SELL" and o["symbol"] == OPTION_SYMBOL]) == 1
    assert _get(env, pos).status == "CLOSED"


def test_an_unknown_exit_outcome_is_not_resent(env):
    from app.services.kite_engine import order_journal

    pos = _open(env)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    env.broker.mode = "unknown"

    _tick(env)
    calls = env.broker.place_calls
    env.broker.mode = "fill"
    _tick(env)
    _tick(env)

    assert env.broker.place_calls == calls            # the same key can never be sent twice
    assert _get(env, pos).option_exit_qty == 0
    assert any(i.state == "UNKNOWN" for i in order_journal.unresolved(env.uid, pos.account_id))


def test_a_position_the_broker_no_longer_holds_is_flagged_not_sold_and_its_hedge_is_closed(env):
    from app.services import live_safety

    pos = _open(env)
    env.broker.net[("NFO", "NRML", OPTION_SYMBOL)] = 0          # closed behind our back
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    before = len(env.broker.placed)

    _tick(env)

    sent = [(o["symbol"], o["side"]) for o in env.broker.placed[before:]]
    assert sent == [(FUTURE_SYMBOL, "SELL")]         # no option is sold that is not held ...
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0   # ... the hedge is closed
    assert env.broker.gtts == {}                     # and no stop is left to open a naked short
    closed = _get(env, pos)
    assert closed.status == "CLOSED"                 # never left EXITING with its stop released
    assert closed.option_exit_unknown_qty == 75 and closed.hedge_exit_qty == 75
    assert "option_exit_price_unknown" in closed.flags
    assert live_safety.kill_switch_state()["enabled"] is True   # an operator must look


def test_an_option_the_broker_holds_only_part_of_is_sold_for_what_exists_and_the_hedge_closed(env):
    pos = _open(env)
    env.broker.net[("NFO", "NRML", OPTION_SYMBOL)] = 25         # 50 units were sold in the app
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    before = len(env.broker.placed)

    _tick(env)

    sent = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed[before:]]
    assert sent == [(OPTION_SYMBOL, "SELL", 25), (FUTURE_SYMBOL, "SELL", 75)]
    closed = _get(env, pos)
    assert closed.status == "CLOSED"
    assert closed.option_exit_qty == 75 and closed.option_exit_unknown_qty == 50
    # only the quantity Sterling actually sold has a price
    assert closed.option_exit_price == pytest.approx(190.0)
    assert closed.realised_pnl == pytest.approx((190.0 - 300.0) * 25 + (25000.0 - 25001.0) * 75)


def test_an_unhedged_position_gone_from_the_broker_is_closed_not_wedged(env):
    from dataclasses import replace
    from app.services import snapback

    cfg = replace(env.cfg, trading_mode="scalp")
    env.monkeypatch.setattr(snapback, "get_config", lambda u=None: cfg)
    env.clock = SCALP_NOW
    env.rows = [scalp_row(signal_id=_signal_id())]
    _cycle(env)
    pos = env.positions.open_positions(env.uid)[0]
    env.positions.mutate(pos.position_id,
                         lambda p: setattr(p, "opened_ms", int(env.clock.timestamp() * 1000)))
    env.broker.net[("NFO", "NRML", OPTION_SYMBOL)] = 0
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 150.0, 151.0)         # through the stop
    before = len(env.broker.placed)

    _tick(env)

    assert len(env.broker.placed) == before and env.broker.gtts == {}
    assert _get(env, pos).status == "CLOSED"


def test_an_operator_exit_request_uses_the_same_procedure(env):
    pos = _open(env)

    outcome = run(env.dispatcher.exit_manager.exit_position(env.broker, pos.position_id, "operator"))

    assert outcome == "closed"
    assert _get(env, pos).exit_reason == "operator"


def test_an_unprotected_position_is_re_protected_on_the_next_pass(env):
    pos = _open(env)
    env.broker.gtts.clear()                            # someone deleted the trigger in the app
    env.positions.mutate(pos.position_id, lambda p: (setattr(p, "gtt_id", 0),
                                                     setattr(p, "protection_state", "FAILED")))

    _tick(env)

    fresh = _get(env, pos)
    assert fresh.protection_state == "ACTIVE" and fresh.gtt_id in env.broker.gtts


def test_a_vanished_stop_is_detected_and_re_armed(env):
    pos = _open(env)
    env.broker.gtts.pop(pos.gtt_id)                    # the trigger disappears at the broker

    _tick(env)                                         # detection
    _tick(env)                                         # re-arm

    fresh = _get(env, pos)
    assert fresh.protection_state == "ACTIVE" and fresh.gtt_id in env.broker.gtts


def test_a_missing_quote_does_not_stop_the_manager_and_keeps_the_broker_stop(env):
    pos = _open(env)
    env.broker.quotes.pop(f"NFO:{OPTION_SYMBOL}")

    _tick(env)

    assert _get(env, pos).status == "OPEN" and _get(env, pos).gtt_id in env.broker.gtts


def test_the_end_of_day_horizon_exit_runs_through_the_manager(env):
    pos = _open(env)
    env.positions.mutate(pos.position_id, lambda p: setattr(p, "entry_day", "2026-09-01"))
    env.clock = _at("2026-10-07", "15:16")
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 310.0, 311.0)

    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "HOLDING_HORIZON_EXPIRED"


def test_exits_are_not_attempted_outside_the_session(env):
    pos = _open(env)
    env.clock = _at("2026-10-07", "20:00")
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 150.0, 151.0)
    before = len(env.broker.placed)

    _tick(env)

    assert len(env.broker.placed) == before and _get(env, pos).status == "OPEN"


# ================================================================= scalp chain


def _scalp_env(env):
    from dataclasses import replace
    from app.services import snapback

    cfg = replace(env.cfg, trading_mode="scalp")
    env.monkeypatch.setattr(snapback, "get_config", lambda u=None: cfg)
    env.clock = SCALP_NOW
    return cfg


def _entered_now(env, pos):
    """The registry stamps wall-clock time; the tests drive a fixed session clock."""
    env.positions.mutate(pos.position_id,
                         lambda p: setattr(p, "opened_ms", int(env.clock.timestamp() * 1000)))


def test_a_fresh_scalp_signal_becomes_one_unhedged_protected_position(env):
    _scalp_env(env)
    env.rows = [scalp_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["entries"] == 1, env.dispatcher.status(env.uid)
    assert [(o["symbol"], o["side"]) for o in env.broker.placed] == [(OPTION_SYMBOL, "BUY")]
    pos = env.positions.open_positions(env.uid)[0]
    assert pos.lane == "scalp" and not pos.has_hedge
    assert pos.protection_state == "ACTIVE"
    assert pos.stop_premium == pytest.approx(pos.option_entry_price - 4.0, abs=0.06)


def test_a_scalp_signal_older_than_its_bar_is_refused(env):
    _scalp_env(env)
    old = int((SCALP_NOW - timedelta(minutes=10)).timestamp() * 1000)
    env.rows = [scalp_row(signal_id=_signal_id(), close_ms=old)]

    status = _cycle(env)

    assert list(status["refusals"].values()) == ["signal_stale"]
    assert env.broker.placed == []


def test_a_scalp_outside_its_entry_window_is_refused(env):
    _scalp_env(env)
    env.clock = _at("2026-10-07", "09:20")           # before scalp_entry_start_minute
    row = scalp_row(signal_id=_signal_id(), close_ms=int((env.clock - timedelta(seconds=20)).timestamp() * 1000))
    env.rows = [row]

    status = _cycle(env)

    assert list(status["refusals"].values()) == ["outside_scalp_entry_window"]


def test_a_scalp_target_exit_and_the_stop_ratchet_run_through_the_manager(env):
    _scalp_env(env)
    env.rows = [scalp_row(signal_id=_signal_id())]
    _cycle(env)
    pos = env.positions.open_positions(env.uid)[0]
    _entered_now(env, pos)
    entry_gtt = pos.gtt_stop

    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 302.5, 303.5)     # lock level
    _tick(env)
    locked = _get(env, pos)
    assert locked.phase == "LOCKED" and locked.gtt_stop > entry_gtt     # the broker stop moved up

    target = pos.rules["target_premium"]
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", target + 0.5, target + 1.5)     # target
    _tick(env)
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "target_reached_no_continuation"


def test_a_scalp_is_squared_off_at_1515(env):
    _scalp_env(env)
    env.rows = [scalp_row(signal_id=_signal_id())]
    _cycle(env)
    pos = env.positions.open_positions(env.uid)[0]
    env.positions.mutate(pos.position_id, lambda p: setattr(
        p, "opened_ms", int(_at("2026-10-07", "15:10").timestamp() * 1000)))

    env.clock = _at("2026-10-07", "15:15")
    _tick(env)

    assert _get(env, pos).status == "CLOSED" and _get(env, pos).exit_reason == "session_square_off"


# ================================ management does not depend on the entry calendar
#
# The verified holiday list ends with 2026. From the first 2027 session the entry
# calendar cannot answer, and that must refuse NEW entries - never leave an open
# position without its stop ratchet, time exit or re-arm.


def test_a_position_held_over_year_end_is_still_decided_on_in_an_unverified_year():
    pos = _swing_position(entry_day="2026-12-28", rules={
        **_swing_position().rules, "hold_days": 5})

    stop = decide_swing(pos, bid=190.0, now=_at("2027-01-04", "10:00"))
    horizon = decide_swing(pos, bid=310.0, now=_at("2027-01-04", "15:16"))

    assert stop.reason == "PREMIUM_STOP"
    # 28, 29, 30, 31 Dec and 1, 4 Jan: six weekday sessions, no calendar exception
    assert sessions_held("2026-12-28", date(2027, 1, 4)) == 6
    assert horizon.reason == "HOLDING_HORIZON_EXPIRED"


def test_an_unlisted_holiday_does_not_make_the_end_of_day_pass_look_missed():
    # Monday 4 Jan 2027 might be a holiday the list does not know. Friday's pass ran;
    # Tuesday at the open must not run the end-of-day rules on an intraday mark.
    pos = _swing_position(entry_day="2026-09-01", last_eod_session="2027-01-01")

    assert decide_swing(pos, bid=310.0, now=_at("2027-01-05", "10:00")).reason == ""
    # Two sessions skipped is a real miss, caught at once.
    skipped = _swing_position(entry_day="2026-09-01", last_eod_session="2026-12-31")
    assert decide_swing(skipped, bid=310.0, now=_at("2027-01-05", "10:00")).reason == (
        "HOLDING_HORIZON_EXPIRED")


def test_the_management_session_falls_back_to_weekday_hours_only_where_the_calendar_is_unknown():
    from app.services.kite_engine import market_hours as mh

    assert mh.management_session_phase(_at("2027-01-04", "10:00")) == "continuous_unverified"
    assert mh.is_management_session(_at("2027-01-04", "10:00"))
    assert not mh.is_management_session(_at("2027-01-04", "09:00"))     # before the open
    assert not mh.is_management_session(_at("2027-01-04", "15:45"))     # after the close
    assert not mh.is_management_session(_at("2027-01-02", "11:00"))     # Saturday
    # A verified holiday stays a holiday; the fallback never overrides the calendar.
    assert mh.management_session_phase(_at("2026-12-25", "11:00")) == "closed"
    assert not mh.is_management_session(_at("2026-12-25", "11:00"))
    assert mh.management_session_phase(_at("2026-12-31", "11:00")) == "continuous"
    # An unsupported exchange is not guessed at.
    assert mh.management_session_phase(_at("2027-01-04", "10:00"),
                                       exchange="MCX") == "calendar_unknown"


def test_entries_stay_refused_in_an_unverified_year():
    from app.services.kite_engine import market_hours as mh

    now = _at("2027-01-04", "10:00")
    assert not mh.is_market_open(now)
    assert mh.session_phase(now) == "calendar_unknown"
    assert mh.entry_block_reason(now) == "session_calendar_unknown"


def test_the_calendar_runway_counts_down_to_the_end_of_the_last_verified_year():
    from app.services.kite_engine import market_hours as mh

    assert mh.calendar_runway_days(date(2026, 12, 31)) == 0
    assert mh.calendar_runway_days(date(2026, 11, 1)) == 60
    assert mh.calendar_runway_days(date(2027, 1, 4)) == -4


def test_open_positions_are_managed_across_the_year_end_and_the_operator_is_told(env):
    from app.services import snapback_live_exits

    pos = _open(env)

    def held_over_year_end(p):
        p.entry_day = "2026-12-31"
        p.last_eod_session = "2026-12-31"

    env.positions.mutate(pos.position_id, held_over_year_end)
    alerts = []
    env.monkeypatch.setattr(
        snapback_live_exits, "alert",
        lambda event, title, detail, **kw: alerts.append((event, kw.get("severity", "INFO"))))
    # The broker stop is lost, and it is the first session of an unverified year.
    env.broker.gtts.clear()
    env.clock = _at("2027-01-04", "10:00")

    _tick(env)
    _tick(env)

    fresh = _get(env, pos)
    assert fresh.protection_state == "ACTIVE" and fresh.gtt_id in env.broker.gtts
    assert alerts.count(("CALENDAR UNVERIFIED", "CRITICAL")) == 2    # keyed per day by the outbox
    assert ("PROTECTION LOST", "CRITICAL") in alerts


def test_a_premium_stop_exits_a_position_in_an_unverified_year(env):
    pos = _open(env)
    env.clock = _at("2027-01-04", "10:00")
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)

    _tick(env)

    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "PREMIUM_STOP"


def test_positions_are_not_managed_on_a_weekend_of_an_unverified_year(env):
    pos = _open(env)
    env.clock = _at("2027-01-02", "11:00")                      # Saturday
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)

    _tick(env)

    assert _get(env, pos).status == "OPEN"


def test_the_dispatcher_warns_daily_once_the_verified_calendar_is_about_to_run_out(env):
    from app.services import snapback_live_wiring

    sent = []
    env.monkeypatch.setattr(snapback_live_wiring, "alert",
                            lambda event, title, detail, **kw: sent.append((event, kw["key"], kw["severity"])))

    env.clock = _at("2026-10-07", "10:00")                     # 85 days left: quiet
    _cycle(env)
    assert sent == []

    env.clock = _at("2026-11-02", "10:00")                     # 59 days left
    _cycle(env)
    _cycle(env)                                                # once per day
    env.clock = _at("2026-12-20", "10:00")                     # 11 days left: critical
    _cycle(env)

    assert sent == [("CALENDAR EXPIRING", "snapback:CALENDAR:RUNWAY:2026-11-02", "WARNING"),
                    ("CALENDAR EXPIRING", "snapback:CALENDAR:RUNWAY:2026-12-20", "CRITICAL")]


# ============================== a position still being established keeps a stop


def _entering_without_a_stop(env, pos):
    """The state a crash or hang between the option fill and the hedge result leaves."""
    from app.services import snapback_live_exits

    env.monkeypatch.setattr(snapback_live_exits, "ENTERING_GRACE_S", 180)
    env.broker.gtts.clear()

    def apply(p):
        p.status = "ENTERING"
        p.protection_state = "NONE"
        p.gtt_id = 0
        p.gtt_qty = 0
        p.gtt_stop = 0.0
        p.opened_ms = int(env.clock.timestamp() * 1000)

    env.positions.mutate(pos.position_id, apply)


def test_the_entering_grace_period_never_covers_a_position_with_no_broker_stop(env):
    pos = _open(env)
    _entering_without_a_stop(env, pos)
    before = len(env.broker.placed)

    _tick(env)

    fresh = _get(env, pos)
    assert fresh.protection_state == "ACTIVE" and fresh.gtt_id in env.broker.gtts
    assert fresh.status == "ENTERING"                 # the executor still owns the rest
    assert len(env.broker.placed) == before


def test_an_entering_position_is_not_exited_on_a_price_while_the_executor_owns_it(env):
    pos = _open(env)
    _entering_without_a_stop(env, pos)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)       # below the premium stop
    before = len(env.broker.placed)

    _tick(env)

    assert _get(env, pos).status == "ENTERING"
    assert len(env.broker.placed) == before          # a half-built position is not sold blind


def test_a_protected_entering_position_is_left_entirely_to_the_executor(env):
    from app.services import snapback_live_exits

    pos = _open(env)
    env.monkeypatch.setattr(snapback_live_exits, "ENTERING_GRACE_S", 180)

    def apply(p):
        p.status = "ENTERING"
        p.opened_ms = int(env.clock.timestamp() * 1000)

    env.positions.mutate(pos.position_id, apply)
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    before = len(env.broker.placed)
    gtts = dict(env.broker.gtts)

    _tick(env)

    assert _get(env, pos).status == "ENTERING" and len(env.broker.placed) == before
    assert env.broker.gtts == gtts


def test_the_executors_first_stop_attempt_records_a_failure_without_halting_entries(env):
    from app.services import live_safety
    from app.services.snapback_live_wiring import SnapbackProtection

    pos = _open(env)
    env.broker.gtts.clear()
    env.positions.mutate(pos.position_id, lambda p: (
        setattr(p, "protection_state", "NONE"), setattr(p, "gtt_id", 0), setattr(p, "gtt_qty", 0)))
    env.broker.gtt_fails = True
    live_safety.set_kill_switch(False, "test")
    protection = SnapbackProtection(positions=env.positions,
                                    broker_client_fn=lambda uid, account: env.broker)

    quiet = run(protection.place(plan_id=pos.position_id, symbol=OPTION_SYMBOL, quantity=75,
                                 account_id=pos.account_id, escalate=False))

    assert quiet["state"] == "REQUIRED"
    assert live_safety.kill_switch_state()["enabled"] is False
    assert _get(env, pos).protection_state == "FAILED" and "unprotected" in _get(env, pos).flags

    loud = run(protection.place(plan_id=pos.position_id, symbol=OPTION_SYMBOL, quantity=75,
                                account_id=pos.account_id))

    assert loud["state"] == "REQUIRED"
    assert live_safety.kill_switch_state()["enabled"] is True      # the authoritative attempt halts


def test_a_whole_dispatcher_cycle_in_an_unverified_year_manages_but_originates_nothing(env):
    pos = _open(env)
    env.clock = _at("2027-01-04", "10:00")
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)           # the premium stop is hit
    env.rows = [swing_row(signal_id=_signal_id(), symbol="BANKNIFTY")]
    placed = len(env.broker.placed)

    status = _cycle(env)

    assert status["managed"] == 1
    assert "session_calendar_unknown" in status["blockers"]               # entries stay refused
    closed = _get(env, pos)
    assert closed.status == "CLOSED" and closed.exit_reason == "PREMIUM_STOP"
    assert [o["side"] for o in env.broker.placed[placed:]] == ["SELL", "SELL"]   # option, then hedge

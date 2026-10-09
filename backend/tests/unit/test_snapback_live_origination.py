"""Package D: Snapback originates LIVE orders through the canonical execution path.

These tests run the real chain -- plan producer, dispatcher, live executor,
canonical execution service, order journal, fill ledger, protective stop -- against
a broker double that behaves like a live Kite transport. What they pin:

* a fresh swing signal becomes ONE hedged, protected position;
* a duplicate / restarted signal never becomes a second order;
* an unknown submission outcome is never resent and engages recovery;
* an unconfirmed broker stop engages the kill switch and is retried;
* the kill switch, a stale row, a daily-loss halt or any SafetySupervisor refusal
  stops new entries without touching exits.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.snapback_live_harness import (
    FUTURE_SYMBOL, OPTION_SYMBOL, SCALP_NOW, SWING_NOW, LiveBroker, live_env, scalp_row,
    swing_row,
)
from tests.snapback_live_harness import cycle as _cycle
from tests.snapback_live_harness import run
from tests.snapback_live_harness import signal_id as _signal_id


_ = live_env   # imported so pytest registers the shared ``env`` fixture


# ---------------------------------------------------------------- happy path


def test_a_fresh_swing_signal_becomes_one_hedged_protected_position(env):
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["state"] == "ready", status
    assert status["entries"] == 1, env.dispatcher.status(env.uid)
    placed = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed]
    assert placed == [(OPTION_SYMBOL, "BUY", 75), (FUTURE_SYMBOL, "BUY", 75)]
    pos = env.positions.open_positions(env.uid)[0]
    assert pos.status == "OPEN"
    assert pos.option_qty == 75 and pos.hedge_qty == 75
    assert pos.protection_state == "ACTIVE" and pos.gtt_id in env.broker.gtts
    # the stop is derived from the CONFIRMED fill, not the quote
    assert pos.option_entry_price == pytest.approx(300.0)
    assert pos.stop_premium == pytest.approx(195.0)
    # the entry went out as a marketable limit, in NRML
    option_order = env.broker.placed[0]
    assert option_order["order_type"].lower().startswith("limit")
    assert option_order["limit_price"] >= 300.0
    assert option_order["product"] == "NRML"


def _orders(env):
    return [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed]


# ----------------------------------------------------- one signal, one order


def test_a_duplicate_signal_places_exactly_one_order(env):
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]

    _cycle(env)
    again = _cycle(env)

    assert len(env.broker.placed) == 2          # option + hedge, once
    assert again["refusals"][row["signal_id"]].startswith("plan_exists")


def test_a_restart_never_reopens_a_signal_it_already_traded(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)

    env.dispatcher = env.build_dispatcher()      # a new process, same durable stores
    _cycle(env)
    _cycle(env)

    assert len(env.broker.placed) == 2


def test_two_dispatchers_racing_for_one_signal_place_one_order(env):
    env.rows = [swing_row(signal_id=_signal_id())]
    other = env.build_dispatcher()

    async def race():
        await asyncio.gather(env.dispatcher.cycle(env.uid), other.cycle(env.uid))

    run(race())

    assert len(env.broker.placed) == 2
    assert len(env.positions.open_positions(env.uid)) == 1


def test_the_plan_id_is_derived_from_the_signal_so_a_lost_plan_store_cannot_duplicate(env):
    from app.services.snapback_plan_producer import plan_id_for

    row = swing_row(signal_id=_signal_id())
    env.rows = [row]
    _cycle(env)

    assert plan_id_for(row["signal_id"]) == plan_id_for(row["signal_id"])
    assert plan_id_for(row["signal_id"]) != plan_id_for(row["signal_id"] + "x")
    assert env.plan_store.get(plan_id_for(row["signal_id"]))["status"] == "CONSUMED"


# ------------------------------------------------ unknown outcome: no resend


def test_an_unknown_submission_is_never_resent_and_requires_recovery(env):
    from app.services import db

    env.broker.mode = "unknown"
    row = swing_row(signal_id=_signal_id())
    env.rows = [row]

    _cycle(env)

    assert env.broker.place_calls == 1
    plans = env.plan_store.list_in_flight(owner_uid=env.uid)
    assert [p["status"] for p in plans] == ["RECONCILING"]
    control = db.get_execution_control(uid=env.uid, account_id=env.broker._account_id)
    assert control["recovery_state"] == "RECOVERY_REQUIRED"

    env.broker.mode = "fill"       # the broker recovers; Sterling still must not resend
    _cycle(env)
    _cycle(env)

    assert env.broker.place_calls == 1
    assert env.positions.open_positions(env.uid) == []


def test_a_rejected_entry_leaves_nothing_behind(env):
    env.broker.mode = "reject"
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    assert env.positions.open_positions(env.uid) == []
    assert env.broker.gtts == {}
    assert env.broker.placed and all(o["symbol"] == OPTION_SYMBOL for o in env.broker.placed)


# ------------------------------------------------- protection unconfirmed


def test_an_unconfirmed_broker_stop_engages_the_kill_switch_and_is_retried(env):
    from app.services import live_safety

    env.broker.gtt_refused = True                # a definitive 4xx: nothing was placed, nothing is fenced
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    pos = env.positions.open_positions(env.uid)[0]
    assert pos.protection_state == "FAILED"
    assert "unprotected" in pos.flags
    assert live_safety.kill_switch_state()["enabled"] is True
    assert [p["status"] for p in env.plan_store.list_in_flight(owner_uid=env.uid)] == ["RECONCILING"]

    env.broker.gtt_refused = False               # the broker accepts triggers again
    again = _cycle(env)

    pos = env.positions.open_positions(env.uid)[0]
    assert pos.protection_state == "ACTIVE" and pos.gtt_id in env.broker.gtts
    assert "unprotected" not in pos.flags
    assert "kill_switch" in again["blockers"]    # ... but the operator must clear the halt
    # recovery sent no second order for either leg
    assert _orders(env) == [(OPTION_SYMBOL, "BUY", 75), (FUTURE_SYMBOL, "BUY", 75)]


# ----------------------------------------------------------- gates refuse


def test_the_kill_switch_stops_entries_but_not_exits(env, monkeypatch):
    from app.core import lane_registry

    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    assert len(env.broker.placed) == 2

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    env.rows = [swing_row(signal_id=_signal_id(), symbol="BANKNIFTY")]
    status = _cycle(env)
    assert "kill_switch" in status["blockers"]
    assert len(env.broker.placed) == 2           # no new entry

    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)    # through the stop
    _cycle(env)

    pos = env.positions.get(env.positions.list(env.uid)[0].position_id)
    assert pos.status == "CLOSED" and pos.exit_reason == "PREMIUM_STOP"
    assert len(env.broker.placed) == 4           # option sold, hedge unwound
    assert env.broker.net.get(("NFO", "NRML", OPTION_SYMBOL), 0) == 0
    assert env.broker.net.get(("NFO", "NRML", FUTURE_SYMBOL), 0) == 0


def test_a_stale_row_is_refused_and_nothing_is_published(env):
    from tests.snapback_live_harness import SWING_SIGNAL_SESSION

    two_days_ago = int((SWING_SIGNAL_SESSION.replace(day=2)).timestamp() * 1000)
    env.rows = [swing_row(signal_id=_signal_id(), timestamp_ms=two_days_ago)]

    status = _cycle(env)

    assert status["refusals"] and list(status["refusals"].values()) == ["signal_not_for_today"]
    assert env.broker.placed == []
    assert env.plan_store.list_in_flight() == []


def test_a_row_the_board_marks_stale_or_modelled_is_not_traded(env):
    env.rows = [swing_row(signal_id=_signal_id(), stale=True),
                swing_row(signal_id=_signal_id(), origin="modelled", premium_is_modelled=True)]

    _cycle(env)

    assert env.broker.placed == []


def test_a_daily_loss_halt_refuses_entries(env, monkeypatch):
    from app.services.kite_engine import state

    monkeypatch.setattr(state, "daily_realized_pnl_strict", lambda uid: -2000.0)
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["state"] == "blocked"
    assert "daily_loss_halt" in status["blockers"]
    assert env.broker.placed == []


def test_safe_mode_refuses_entries(env, monkeypatch, tmp_path):
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    path = tmp_path / "safe.json"
    service = SafeModeService(path)
    service.initialise(operator_ack=True, note="test")
    service.engage(trigger=SafeModeTrigger.OPERATOR, reason="drill")
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert "safe_mode" in status["blockers"]
    assert env.broker.placed == []


def test_unresolved_orders_refuse_entries(env):
    from app.services.kite_engine import order_journal

    stray = order_journal.reserve(
        uid=env.uid, account_id=env.broker._account_id, strategy_id="gamma_move",
        generation_id="g", signal_id="stray", exchange="NFO", symbol="SOMETHING",
        side="BUY", quantity=1, capital_required=100.0, available_capital=10_000.0,
        payload={"product": "NRML"})
    assert order_journal.claim_submission(stray.intent_key)
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert "recovery_required" in status["blockers"]
    assert env.broker.placed == []


def test_the_family_stop_switch_refuses_entries(env):
    from app.services import snapback_family_ops as fam

    fam.set_new_trades_halted(True, reason="drill")
    try:
        env.rows = [swing_row(signal_id=_signal_id())]
        status = _cycle(env)
    finally:
        fam.set_new_trades_halted(False, reason="drill over")

    assert "new_trades_halted" in status["blockers"]
    assert env.broker.placed == []


@pytest.mark.parametrize("health,expected", [
    ({"status": "DEGRADED", "reconciled": True}, "system_not_healthy"),
    ({"status": "HEALTHY", "reconciled": False, "broker_connected": True}, "broker_not_reconciled"),
    # Not logged in to Kite is reported as its own cause, not as "books disagree".
    ({"status": "DEGRADED", "reconciled": False, "broker_connected": False,
      "unresolved_errors": ["broker_disconnected"]}, "no_broker_session"),
])
def test_the_retained_family_gate_conditions_block(env, health, expected):
    env.health.update(health)
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert expected in status["blockers"]
    assert env.broker.placed == []


def test_the_waived_evidence_verdict_does_not_block(env):
    # The fixture's readiness reports evidence INCONCLUSIVE and not promoted.
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["state"] == "ready" and status["entries"] == 1


def test_both_auto_switches_are_required(env, monkeypatch):
    from app.services.kite_engine import state as engine_state

    env.rows = [swing_row(signal_id=_signal_id())]
    monkeypatch.setattr(engine_state, "get_config", lambda u: SimpleNamespace(auto_execute=False))

    assert _cycle(env)["state"] == "auto_off"
    assert env.broker.placed == []

    monkeypatch.setattr(engine_state, "get_config", lambda u: SimpleNamespace(auto_execute=True))
    from dataclasses import replace
    from app.services import snapback

    monkeypatch.setattr(snapback, "get_config", lambda u=None: replace(env.cfg, auto_execute=False))
    assert _cycle(env)["state"] == "auto_off"
    assert env.broker.placed == []


def test_a_disabled_engine_originates_nothing_but_still_manages_positions(env, monkeypatch):
    from dataclasses import replace
    from app.services import snapback

    env.rows = [swing_row(signal_id=_signal_id())]
    _cycle(env)
    assert len(env.positions.open_positions(env.uid)) == 1

    monkeypatch.setattr(snapback, "get_config", lambda u=None: replace(env.cfg, enabled=False))
    env.rows = [swing_row(signal_id=_signal_id(), symbol="BANKNIFTY")]
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 190.0, 191.0)
    status = _cycle(env)

    assert status["state"] == "engine_disabled"
    assert len(env.broker.placed) == 4           # the exit still ran
    assert env.positions.open_positions(env.uid) == []


def test_no_broker_session_refuses_without_error(env):
    env.dispatcher._client_factory = lambda uid: None
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["state"] == "no_broker_session"
    assert env.broker.placed == []


def test_an_unknown_broker_mode_is_refused(env):
    env.broker._is_paper = None
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    assert env.broker.placed == []
    assert env.positions.open_positions(env.uid) == []


def test_insufficient_capital_is_refused_before_any_order(env):
    env.broker.capital = 10_000.0                 # a lot of this option costs 22,500
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert env.broker.placed == []
    assert list(status["refusals"].values())[0] in ("NO_CAPACITY", "insufficient_capital")


def test_a_hedge_that_cannot_be_sized_refuses_the_entry(env):
    """A single RELIANCE lot is ~10 NIFTY units of hedge: under one future lot."""
    from tests.snapback_live_harness import SMALL_OPTION_SYMBOL

    env.rows = [swing_row(signal_id=_signal_id("snapback:fade_up:RELIANCE"), symbol="RELIANCE",
                          option_symbol=SMALL_OPTION_SYMBOL, spot=1500.0, lot_size=250,
                          cash_tradingsymbol="RELIANCE", ask=100.0)]

    status = _cycle(env)

    assert list(status["refusals"].values()) == ["hedge_lots_zero"]
    assert env.broker.placed == []


def test_a_wide_spread_quote_is_refused_at_plan_time(env):
    env.broker.set_quote(f"NFO:{OPTION_SYMBOL}", 250.0, 300.0)     # 18% wide
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert list(status["refusals"].values()) == ["quote_not_executable"]
    assert env.broker.placed == []


def test_a_fade_down_call_is_hedged_with_a_short_future(env):
    env.broker.quotes["NFO:NIFTY26NOV25000CE"] = (299.0, 300.0, 299.5)
    row = swing_row(signal_id=_signal_id("snapback:fade_down:NIFTY"), option_type="CE",
                    side="fade_down", delta=0.7, option_symbol="NIFTY26NOV25000CE")
    env.rows = [row]

    _cycle(env)

    assert [(s, side) for s, side, _ in _orders(env)] == [
        ("NIFTY26NOV25000CE", "BUY"), (FUTURE_SYMBOL, "SELL")]
    pos = env.positions.open_positions(env.uid)[0]
    assert pos.hedge_side == "SELL"


def test_a_partial_entry_fill_is_hedged_and_protected_at_the_confirmed_size(env):
    env.broker.mode = "partial"
    env.broker.hedge_mode = "fill"
    # two lots requested so a half fill is still a whole lot
    from dataclasses import replace
    from app.services import snapback

    cfg = replace(env.cfg, lots=2, sizing_mode="LOTS")
    env.monkeypatch.setattr(snapback, "get_config", lambda u=None: cfg)
    env.rows = [swing_row(signal_id=_signal_id())]

    _cycle(env)

    pos = env.positions.open_positions(env.uid)[0]
    assert pos.option_qty == 75                  # 150 requested, 75 confirmed
    assert pos.gtt_qty == 75                     # protected at the confirmed size
    assert env.broker.cancelled_orders           # the unfilled remainder was cancelled

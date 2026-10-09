"""Golden strings for the Trade Journal's wording and the canonical exit-reason mapping."""
from __future__ import annotations

import pytest

from app.services.trade_journal_narrative import (
    CANONICAL_EXIT_REASONS, build_narrative, canonical_exit_reason, classify_skip, reason_for_code,
)
from tj_support import ms


def _supertrend(**over):
    t = dict(
        strategy="supertrend", symbol="NIFTY25OCT25000CE", underlying="NIFTY", option_type="CE",
        strike=25000.0, side="BUY", lots=1, lot_size=75, qty=75, status="CLOSED", entry_price=120.0,
        entry_ts_ms=ms(10, 20), initial_sl=96.0, target=150.0,
        tsl_history=[{"ts_ms": ms(11, 20), "from": 96.0, "to": 118.0}],
        exit_price=150.0, exit_ts_ms=ms(12, 0), exit_reason="TARGET", gross_pnl=2310.0, net_pnl=2250.0,
        charges=60.0, charges_source="broker",
        signal_context={"regime": "BULL", "aligned": 3, "adx": 28.2})
    t.update(over)
    return t


def test_the_brief_example_reads_as_specified():
    assert build_narrative(_supertrend()) == (
        "Bought 1 lot NIFTY 25000 CE at 120.00 on 3 aligned bullish SuperTrends (ADX 28). "
        "Stop-loss 96.00, target 150.00. Stop was raised to 118.00 at 11:20. "
        "Sold at 150.00 on target, +₹2,250 after charges.")


def test_estimated_charges_are_said_to_be_estimated():
    story = build_narrative(_supertrend(charges_source="estimate"))
    assert story.endswith("+₹2,250 after estimated charges.")


def test_unknown_charges_say_before_charges():
    story = build_narrative(_supertrend(charges=None, charges_source="", net_pnl=2310.0))
    assert story.endswith("Sold at 150.00 on target, +₹2,310 before charges.")


def test_a_losing_stop_out_without_a_trail():
    story = build_narrative(_supertrend(
        tsl_history=[], exit_reason="SL", exit_price=96.0, net_pnl=-1840.0, gross_pnl=-1800.0, target=None))
    assert story == (
        "Bought 1 lot NIFTY 25000 CE at 120.00 on 3 aligned bullish SuperTrends (ADX 28). "
        "Stop-loss 96.00, no fixed target. "
        "Sold at 96.00 when the stop-loss was hit, -₹1,840 after charges.")


def test_several_stop_raises_are_summarised():
    story = build_narrative(_supertrend(tsl_history=[
        {"ts_ms": ms(10, 50), "from": 96.0, "to": 105.0},
        {"ts_ms": ms(11, 20), "from": 105.0, "to": 118.0}]))
    assert "Stop was raised 2 times, last to 118.00 at 11:20." in story


def test_an_open_trade_is_still_open():
    story = build_narrative(_supertrend(status="OPEN", exit_price=None, exit_ts_ms=None, exit_reason="",
                                        gross_pnl=None, net_pnl=None, tsl_history=[]))
    assert story.endswith("Stop-loss 96.00, target 150.00. Still open.")


def test_a_pending_order_has_no_fill_price_yet():
    story = build_narrative(_supertrend(status="PENDING", entry_price=None, exit_price=None, tsl_history=[],
                                        exit_reason="", net_pnl=None, gross_pnl=None))
    assert story.startswith("Order for 1 lot NIFTY 25000 CE placed on 3 aligned bullish SuperTrends (ADX 28).")
    assert story.endswith("Order placed; waiting for the fill.")


def test_a_rejected_order_carries_the_broker_reason():
    story = build_narrative(_supertrend(status="REJECTED", entry_price=None, extra={"reject_reason": "RMS: margin"},
                                        tsl_history=[], exit_reason="", net_pnl=None, gross_pnl=None))
    assert story.endswith("The order was not filled (RMS: margin).")


def test_a_trade_held_overnight_names_the_exit_day():
    story = build_narrative(_supertrend(exit_ts_ms=ms(15, 20, day=14), exit_reason="EOD"))
    assert "Sold at 150.00 at the end-of-day exit (14 Oct 15:20), +₹2,250 after charges." in story


def test_a_short_futures_trade_is_sold_then_bought_back():
    story = build_narrative(dict(
        strategy="supertrend", symbol="NIFTY25OCTFUT", underlying="NIFTY", option_type="FUT", side="SELL",
        lots=2, lot_size=75, qty=150, status="CLOSED", entry_price=25000.0, entry_ts_ms=ms(9, 30),
        initial_sl=25100.0, exit_price=24900.0, exit_ts_ms=ms(13, 0), exit_reason="SIGNAL_EXIT",
        net_pnl=14800.0, gross_pnl=15000.0, charges=200.0, charges_source="broker",
        signal_context={"regime": "BEAR", "aligned": 3}))
    assert story == (
        "Sold short 2 lots NIFTY futures at 25000.00 on 3 aligned bearish SuperTrends. "
        "Stop-loss 25100.00, no fixed target. "
        "Bought back at 24900.00 when the exit signal fired, +₹14,800 after charges.")


def test_a_closed_trade_with_no_exit_price_says_so():
    story = build_narrative(_supertrend(exit_price=None, exit_reason="RECONCILED", gross_pnl=None, net_pnl=None,
                                        charges=None, charges_source="", tsl_history=[]))
    assert story.endswith("Closed after it was closed outside Sterling; exit price not recorded. "
                          "P&L not recorded.")


def test_gamma_entry_reason():
    story = build_narrative(dict(
        strategy="gamma", symbol="NIFTY25OCT25000CE", underlying="NIFTY", option_type="CE", strike=25000.0,
        side="BUY", lots=1, lot_size=75, qty=75, status="OPEN", entry_price=120.0, entry_ts_ms=ms(11, 0),
        initial_sl=98.0, target=150.0,
        signal_context={"tier": "ENTRY", "oi_drop_pct": 9.5, "vol_ratio": 1.8, "zone_edge": 25020.0,
                        "gates_passed": ["zone", "liquidity"], "hold": "intraday"}))
    assert story == (
        "Bought 1 lot NIFTY 25000 CE at 120.00 on a Gamma Unwind E1 entry signal (open interest fell 9.5%; "
        "volume 1.8x average; zone edge 25020; gates passed: zone, liquidity; intraday hold). "
        "Stop-loss 98.00, target 150.00. Still open.")


def test_snapback_entry_reason_with_hedge():
    story = build_narrative(dict(
        strategy="snapback", symbol="NIFTY25NOV25000PE", underlying="NIFTY", option_type="PE", strike=25000.0,
        side="BUY", lots=1, lot_size=75, qty=75, status="OPEN", entry_price=300.0, entry_ts_ms=ms(9, 40),
        initial_sl=195.0,
        signal_context={"lane": "swing", "side": "fade_up", "strength": "STRONG", "stretch": 2.1,
                        "distance_pct": 3.3, "hedge": {"symbol": "NIFTY25NOVFUT", "qty": 75}}))
    assert story == (
        "Bought 1 lot NIFTY 25000 PE at 300.00 on a Snapback swing signal fading an up-move "
        "(STRONG, stretch 2.1, 3.3% from the mean), hedged with NIFTY25NOVFUT x75. "
        "Stop-loss 195.00, no fixed target. Still open.")


def test_a_missing_context_just_drops_the_why():
    story = build_narrative(_supertrend(signal_context={}, tsl_history=[]))
    assert story.startswith("Bought 1 lot NIFTY 25000 CE at 120.00. Stop-loss 96.00, target 150.00.")


@pytest.mark.parametrize("strategy,raw,trailed,expected", [
    # Supertrend
    ("supertrend", "trail breach @ ₹150.00 ≤ ₹148.00", True, "TSL"),
    ("supertrend", "trail breach @ ₹95.00 ≤ ₹96.00", False, "SL"),
    ("supertrend", "red count exit 3/3 (three_red_signal + counter-signal)", None, "SIGNAL_EXIT"),
    ("supertrend", "target reached @ ₹150.00 ≥ ₹150.00", None, "TARGET"),
    ("supertrend", "expiry square-off (T-1, exp 2026-10-15)", None, "EXPIRY"),
    ("supertrend", "time stop (6≥6 bars held)", None, "TIME_STOP"),
    ("supertrend", "manual exit from the board", None, "MANUAL"),
    ("supertrend", "emergency_square_off", None, "SQUARE_OFF"),
    ("supertrend", "emergency_halt", None, "SQUARE_OFF"),
    ("supertrend", "broker GTT triggered; fill @ ₹95.00", False, "SL"),
    ("supertrend", "broker GTT triggered; fill @ ₹118.00", True, "TSL"),
    ("supertrend", "broker GTT triggered; fill @ ₹118.00", None, "BROKER_STOP"),
    ("supertrend", "reconciled closed at broker (exit price unknown)", None, "RECONCILED"),
    # Gamma Unwind
    ("gamma", "SL", None, "SL"),
    ("gamma", "TRAIL", None, "TSL"),
    ("gamma", "T2", None, "TARGET"),
    ("gamma", "EOD", None, "EOD"),
    ("gamma", "EXPIRY", None, "EXPIRY"),
    ("gamma", "DAY5", None, "TIME_STOP"),
    ("gamma", "SWING_STOP", None, "SL"),
    ("gamma", "STOP_UNFILLED", None, "SL"),
    ("gamma", "operator", None, "SQUARE_OFF"),
    ("gamma", "SL (broker stop; fill unconfirmed)", None, "SL"),
    ("gamma", "not at broker on reconcile", None, "RECONCILED"),
    # Snapback
    ("snapback", "PREMIUM_STOP", False, "SL"),
    ("snapback", "PREMIUM_STOP", True, "TSL"),
    ("snapback", "HOLDING_HORIZON_EXPIRED", None, "TIME_STOP"),
    ("snapback", "RUNNER_TRAIL_STOP", None, "TSL"),
    ("snapback", "EXPIRY_APPROACHING", None, "EXPIRY"),
    ("snapback", "BROKER_STOP", None, "BROKER_STOP"),
    ("snapback", "BROKER_STOP", False, "SL"),
    ("snapback", "BROKER_STOP_UNFILLED", True, "TSL"),
    ("snapback", "horizon_hard_exit", None, "TIME_STOP"),
    ("snapback", "session_square_off", None, "EOD"),
    ("snapback", "stop_loss_hit", False, "SL"),
    ("snapback", "initial_hold_timeout", None, "TIME_STOP"),
    ("snapback", "runner_max_hold_timeout", None, "TIME_STOP"),
    ("snapback", "target_reached_no_continuation", None, "TARGET"),
    ("snapback", "FLATTEN_OPERATOR", None, "SQUARE_OFF"),
    ("snapback", "hedge_missing_at_broker", None, "HEDGE"),
    # unknown strings stay visible as OTHER, never dropped
    ("supertrend", "", None, "OTHER"),
    ("supertrend", "something new", None, "OTHER"),
])
def test_every_strategy_exit_reason_maps_to_the_canonical_set(strategy, raw, trailed, expected):
    got = canonical_exit_reason(strategy, raw, trailed=trailed)
    assert got == expected and got in CANONICAL_EXIT_REASONS


def test_a_canonical_value_maps_to_itself():
    for reason in ("TSL", "TARGET", "EOD", "TIME_STOP", "SIGNAL_EXIT", "MANUAL", "SQUARE_OFF", "EXPIRY", "HEDGE"):
        assert canonical_exit_reason("gamma", reason) == reason


def test_supertrend_refusals_are_classified_with_plain_reasons():
    code, reason = classify_skip("supertrend", "NIFTY: daily loss ₹5000 ≥ 2% of ₹200000 — new entries halted for today")
    assert code == "daily_loss_halt" and "daily loss limit" in reason
    assert classify_skip("supertrend", "unreconciled_exit_or_pnl")[0] == "unreconciled_exit_or_pnl"
    assert classify_skip("supertrend", "NIFTY entry skipped — ADX 18.0 < 25.0")[0] == "adx_below_min"
    assert classify_skip("supertrend", "NIFTY 50: stale_or_unclosed_signal")[0] == "stale_or_unclosed_signal"
    code, reason = classify_skip("supertrend", "a message nobody wrote a pattern for")
    assert code == "blocked" and reason == "a message nobody wrote a pattern for"


def test_reason_for_code_prefers_known_text_then_the_callers_words_then_a_humanised_code():
    assert reason_for_code("auto_off") == "Automatic trading is off for this strategy."
    assert reason_for_code("plan_exists:OPEN", "fallback") == "fallback"
    assert reason_for_code("lane_not_open") == "The strategy's lane is not open to originate (not open)."
    assert reason_for_code("weird_new_code") == "Weird new code."

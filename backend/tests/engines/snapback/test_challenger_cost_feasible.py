"""Distinct cost-feasible challenger: causal data, costs, refusals, no promotion."""
from dataclasses import replace
from datetime import datetime
import numpy as np
import pytest

from app.engines.snapback.challenger import (
    CHALLENGER_ID, assess_cost_feasible_plan, prior_premium_excursions,
)
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday import make_plan, replay_intraday
from app.engines.snapback.models import Bars, IST, SnapbackSignal
from app.engines.snapback import intraday


START = datetime(2026, 9, 14, 9, 15, tzinfo=IST).timestamp()
CFG = replace(SnapbackConfig(), trading_mode="scalp", scalp_max_hold_bars=1)


def candles(n=90, *, rise=12.0, after=None):
    times = START + np.arange(n, dtype=float) * 60
    opens = np.full(n, 100.0)
    highs = opens + rise
    if after is not None:
        highs[-1] = after
    closes = opens.copy()
    lows = opens - 1
    return Bars(times, opens, highs, lows, closes, np.full(n, 1000.0))


def assess(b, plan=None, i=60):
    return assess_cost_feasible_plan(
        plan or make_plan(100, 50, CFG),
        times=b.time[:i], opens=b.open[:i],
        highs=b.high[:i], closes=b.close[:i],
        entry_time=b.time[i], bar_seconds=60,
        horizon_bars=20,
    )


def test_candidate_pays_extra_roundtrip_cost_in_risk_and_realized_pnl():
    b = candles(rise=12.0)
    old = make_plan(100, 50, CFG)
    new = assess(b)
    assert old["accepted"] and new["accepted"]
    assert new["candidate_policy"] == CHALLENGER_ID
    assert new["research_only"]
    assert new["effective_target_points"] > old["effective_target_points"]
    assert new["additional_cost_stress_points"] == 0.5
    assert new["estimated_variable_cost_points"] == old["estimated_variable_cost_points"] + 0.5
    assert new["estimated_total_cost_inr"] == pytest.approx(old["estimated_total_cost_inr"] + 25)
    assert new["estimated_net_target_inr"] == pytest.approx(
        (new["effective_target_points"] - new["estimated_variable_cost_points"]) * 50
        - new["estimated_fixed_cost_inr"])
    assert new["net_reward_risk"] >= 1.0


def test_candidates_reject_target_unattainable_in_prior_closed_premium_candles():
    bad = assess(candles(rise=2.0))
    assert not bad["accepted"]
    assert bad["quantity"] == 0
    assert bad["rejection_reasons"] == ["cost_stressed_target_exceeds_prior_excursion_p75"]
    assert bad["prior_excursion_p75_points"] == 2.0


def test_challenger_has_no_future_candle_lookahead():
    b = candles(rise=12.0)
    old = assess(b, i=60)
    later = replace(b, high=b.high.copy(), close=b.close.copy())
    later.high[60:] = 999999.0
    later.close[60:] = 999998.0
    newer = assess(later, i=60)
    assert old == newer


def test_challenger_rejects_missing_prior_windows_or_bad_timestamp():
    b = candles(n=25)
    out = assess(b, i=24)
    assert not out["accepted"]
    assert "insufficient_causal_option_premium_history" in out["rejection_reasons"]
    invalid = prior_premium_excursions(
        b.time[:60], b.open[:60], b.high[:60], b.close[:60],
        entry_time=b.time[10], bar_seconds=60, horizon_bars=20)
    assert invalid == (None, 0)


def test_one_bar_timeout_must_not_use_three_bar_price_excursion():
    b = candles(n=60, rise=3.0)
    highs = b.high.copy()
    highs[::4] = 115.0  # rare intrabar spikes spread over time
    b = replace(b, high=highs)
    strict, n_one = prior_premium_excursions(
        b.time, b.open, b.high, b.close,
        entry_time=START + 60*60, bar_seconds=60,
        horizon_bars=1)
    optimistic, n_three = prior_premium_excursions(
        b.time, b.open, b.high, b.close,
        entry_time=START + 60*60, bar_seconds=60,
        horizon_bars=3)
    assert (n_one, n_three) == (60, 58)
    assert strict == 3.0 and optimistic == 15.0

    baseline = make_plan(100, 50, CFG)
    restricted = assess_cost_feasible_plan(
        baseline, times=b.time, opens=b.open, highs=b.high,
        closes=b.close, entry_time=START+60*60,
        bar_seconds=60, horizon_bars=CFG.scalp_max_hold_bars)
    overstated = assess_cost_feasible_plan(
        baseline, times=b.time, opens=b.open, highs=b.high,
        closes=b.close, entry_time=START+60*60,
        bar_seconds=60, horizon_bars=3)
    assert not restricted["accepted"]
    assert restricted["rejection_reasons"] == [
        "cost_stressed_target_exceeds_prior_excursion_p75"]
    assert overstated["accepted"]


def test_challenger_rejects_cost_stress_beyond_daily_risk_budget():
    p = {**make_plan(100, 50, CFG), "risk_budget_inr": 295.0}
    out = assess(candles(), plan=p)
    assert not out["accepted"]
    assert "additional_cost_exceeds_risk_budget" in out["rejection_reasons"]


@pytest.mark.parametrize("rr_override", [0.0, 0.5, 0.99])
def test_challenger_never_weakens_net_reward_to_risk_below_one(rr_override):
    b = candles(rise=12.0)
    baseline = make_plan(100, 50, CFG)
    candidate = assess_cost_feasible_plan(
        baseline, times=b.time[:60], opens=b.open[:60],
        highs=b.high[:60], closes=b.close[:60],
        entry_time=b.time[60], bar_seconds=60,
        horizon_bars=CFG.scalp_max_hold_bars, min_rr=rr_override)
    assert not candidate["accepted"]
    assert candidate["quantity"] == 0
    assert "invalid_challenger_economic_inputs" in candidate["rejection_reasons"]


def test_challenger_refuses_when_current_source_candles_are_discontinuous():
    b = candles()
    gap = replace(b, time=b.time.copy())
    gap.time[45:60] += 300
    # Gapped windows are never interpolated into profitable candles.
    ceiling, count = prior_premium_excursions(
        gap.time[:60], gap.open[:60], gap.high[:60], gap.close[:60],
        entry_time=b.time[60], bar_seconds=60, horizon_bars=20)
    assert (ceiling, count) == (None, 0)


def test_extra_execution_cost_is_deducted_from_actual_replay_trade_pnl():
    old = make_plan(100, 50, CFG)
    new = assess(candles())
    assert new["accepted"]
    left = intraday.open_trade(old, START)
    right = intraday.open_trade(new, START)
    # Both exit at the same opening gap-stop mark; only the modelled
    # execution cost differs. It must appear in the CLOSED net result.
    base = intraday.advance_trade(
        left, timestamp=START + 60, open=90, high=95,
        low=89, close=90, cfg=CFG)
    challenger = intraday.advance_trade(
        right, timestamp=START + 60, open=90, high=95,
        low=89, close=90, cfg=CFG)
    assert base.exited and challenger.exited
    assert base.exit_price == challenger.exit_price == 90
    assert challenger.net_pnl_inr == pytest.approx(base.net_pnl_inr - 0.5 * 50)


def test_500_synthetic_cost_scenarios_preserve_declared_risk_and_reward_floor():
    import random
    gen = random.Random(20261027)
    b = candles(rise=35.0)
    accepted = 0
    for _ in range(500):
        variable = gen.uniform(0, 5)
        fixed = gen.uniform(0, 100)
        stop = gen.uniform(0.1, 7)
        risk_budget = gen.uniform(200, 2500)
        requested = gen.uniform(1, 25)
        rr = gen.uniform(1, 2.25)
        extra = gen.uniform(0, 2)
        baseline = {
            **make_plan(100, 50, CFG),
            "estimated_variable_cost_points": variable,
            "estimated_fixed_cost_inr": fixed,
            "stop_points": stop,
            "risk_budget_inr": risk_budget,
            "effective_target_points": requested,
        }
        candidate = assess_cost_feasible_plan(
            baseline, times=b.time[:65], opens=b.open[:65],
            highs=b.high[:65], closes=b.close[:65],
            entry_time=b.time[65], bar_seconds=60, horizon_bars=1,
            min_rr=rr, extra_cost_points=extra)
        if candidate["accepted"]:
            accepted += 1
            assert candidate["estimated_stop_loss_inr"] <= risk_budget + 1e-6
            assert candidate["net_reward_risk"] + 1e-7 >= rr
            assert candidate["effective_target_points"] <= (
                candidate["prior_excursion_p75_points"] + 1e-8)
            assert candidate["estimated_variable_cost_points"] == pytest.approx(variable + extra)
    assert accepted > 50


def test_new_replay_identity_never_overwrites_baseline(monkeypatch):
    b = candles(n=76)
    def signal(series, cfg, symbol="", asof=None):
        if float(series.time[-1]) != float(b.time[50]):
            return []
        return [SnapbackSignal(
            symbol="NIFTY", side="fade_up", direction="BEARISH",
            option_type="PE", timestamp_ms=int(b.time[50]*1000),
            entry=100, mean_target=98, stretch=2, atr=1,
            realized_vol=0.3, assumed_iv=0.3, level=102,
            strength="MODERATE")]
    monkeypatch.setattr(intraday, "evaluate_intraday", signal)
    baseline = replay_intraday(b, b, CFG, "NIFTY", option_type="PE",
                               lot_size=50, option_symbol="NIFTY_FAKE_PE")
    challenger = replay_intraday(b, b, CFG, "NIFTY", option_type="PE",
                                 lot_size=50, option_symbol="NIFTY_FAKE_PE",
                                 candidate_policy=CHALLENGER_ID)
    assert baseline["candidate_policy"] == "baseline"
    assert challenger["candidate_policy"] == CHALLENGER_ID
    assert challenger["candidate_policy_is_independent"]
    assert challenger["hedge_realized_pnl_inr"] is None
    assert baseline["plans"]
    assert challenger["plans"]
    assert challenger["plans"][0]["estimated_variable_cost_points"] > baseline["plans"][0]["estimated_variable_cost_points"]
    assert challenger["plans"][0]["effective_target_points"] > baseline["plans"][0]["effective_target_points"]


def test_unrecognized_policy_cannot_change_replay_behavior():
    b = candles()
    with pytest.raises(ValueError, match="Unknown Snapback challenger"):
        replay_intraday(b, b, CFG, "NIFTY", option_type="PE", lot_size=50,
                        candidate_policy="quietly_enable_live")

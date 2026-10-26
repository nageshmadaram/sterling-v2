"""Snapback research summaries must never hide contract failures or open positions."""
from __future__ import annotations

from app.engines.snapback.research_reporting import (
    choose_option_contract, contract_replay_summary, mode_totals, rejected_plan_count,
)
import pytest


def replay(**changes):
    result = dict(
        research_only=True, trade_count=3, win_rate_pct=66.666,
        net_pnl_inr=1250.65, return_pct=1.25, max_realized_drawdown_inr=200.2,
        rejected_plans=5, complete=True, unresolved_position=None,
        unresolved_reason=None,
    )
    return {**result, **changes}


def test_integer_rejected_plans_is_not_asked_for_len():
    # Oct 22 contract exceptions occurred when the count was treated as a list.
    row = contract_replay_summary(replay(rejected_plans=7))
    assert row["rejected_plans"] == 7
    assert row["trades"] == 3
    assert row["replay_complete"]
    assert row["evidence_type"] == "REAL_MARKET_SIM"


def test_older_list_shaped_rejections_are_counted_explicitly():
    assert rejected_plan_count(["late", "spread"]) == 2


@pytest.mark.parametrize("invalid", [-1, None, True, 3.2, "4", {"count": 4}])
def test_invalid_rejection_types_fail_closed(invalid):
    with pytest.raises(ValueError, match="rejected_plans"):
        rejected_plan_count(invalid)


def test_open_position_does_not_count_toward_profitable_totals():
    closed = {"option": "A-PE", **contract_replay_summary(replay())}
    open_result = contract_replay_summary(replay(
        net_pnl_inr=45000, complete=False,
        unresolved_position={"symbol": "B-PE"}, unresolved_reason="no final quote"))
    assert not open_result["replay_complete"]
    report = mode_totals([closed, {"option": "B-PE", **open_result}])
    assert report["contracts"] == 1
    assert report["contracts_attempted"] == 2
    assert report["contracts_unresolved"] == 1
    assert report["net_inr"] == 1251
    assert report["research_complete"] is False
    assert not report["promotion_eligible"]
    assert not report["broker_verified"]


def test_failed_and_skipped_contracts_remain_in_denominator():
    complete = {"option": "A-PE", **contract_replay_summary(replay())}
    report = mode_totals([
        complete, {"option": "B-CE", "error": "data parse failed"},
        {"option": "C-PE", "skipped": "missing candles"},
    ])
    assert report["contracts_attempted"] == 3
    assert report["contracts"] == 1
    assert report["contract_errors"] == 1
    assert report["contracts_skipped"] == 1
    assert not report["research_complete"]


@pytest.mark.parametrize("bad", [
    {"trade_count": -1},
    {"net_pnl_inr": float("nan")},
    {"win_rate_pct": 200},
    {"max_realized_drawdown_inr": -4},
    {"research_only": False},
])
def test_corrupt_replay_metrics_fail_closed(bad):
    with pytest.raises(ValueError):
        contract_replay_summary(replay(**bad))


def test_missing_or_partial_session_coverage_cannot_be_called_complete():
    done = {"option": "A-PE", **contract_replay_summary(replay())}
    no_coverage = mode_totals([done])
    assert no_coverage["contracts_with_incomplete_coverage"] == 1
    assert no_coverage["research_complete"] is False

    complete_days_only = dict(done, coverage={
        "all_sessions_complete": False,
        "requested_sessions": 20, "complete_sessions": 15,
    })
    partial = mode_totals([complete_days_only])
    assert partial["trades"] == 3  # explicitly research-only closed trades
    assert partial["contracts_with_incomplete_coverage"] == 1
    assert not partial["research_complete"]

    complete = dict(done, coverage={
        "all_sessions_complete": True,
        "requested_sessions": 20, "complete_sessions": 20,
    })
    fully_observed = mode_totals([complete])
    assert fully_observed["contracts_with_incomplete_coverage"] == 0
    assert fully_observed["research_complete"]
    assert fully_observed["promotion_eligible"] is False


def test_empty_sample_is_not_accepted():
    result = mode_totals([])
    assert result["contracts_attempted"] == 0
    assert result["research_complete"] is False
    assert result["avg_win_rate_pct"] is None


def test_nearest_option_contract_selection_skips_corrupt_metadata():
    candidates = [
        {"strike": "bad", "instrument_token": 1, "lot_size": 75, "tradingsymbol": "BAD"},
        {"strike": 100, "instrument_token": 0, "lot_size": 75, "tradingsymbol": "ZERO"},
        {"strike": 101, "instrument_token": 123, "lot_size": 0, "tradingsymbol": "NOLOT"},
        {"strike": 101, "instrument_token": 124, "lot_size": 75, "tradingsymbol": "VALID"},
        {"strike": 107, "instrument_token": 125, "lot_size": 75, "tradingsymbol": "FAR"},
    ]
    assert choose_option_contract(candidates, 100)["tradingsymbol"] == "VALID"
    assert choose_option_contract(candidates[:3], 100) is None


def test_nearest_contract_selection_is_deterministic_and_fail_closed():
    a = {"strike": 99, "instrument_token": 123, "lot_size": 75, "tradingsymbol": "LOWER"}
    b = {"strike": 101, "instrument_token": 124, "lot_size": 75, "tradingsymbol": "UPPER"}
    assert choose_option_contract([a, b], 100)["tradingsymbol"] == "LOWER"
    assert choose_option_contract([b, a], 100)["tradingsymbol"] == "LOWER"
    assert choose_option_contract([a, b], float("nan")) is None
    assert choose_option_contract([{"strike": 100, "instrument_token": True,
                                   "lot_size": 75, "tradingsymbol": "BAD"}], 100) is None

"""Snapback research summaries must never hide contract failures or open positions."""
from __future__ import annotations

from app.engines.snapback.research_reporting import (
    choose_option_contract, contract_replay_summary, eligible_expiries,
    mode_totals, rejected_plan_count, write_research_report_once,
    incremental_execution_cost_stress, closed_trade_audit,
    current_instrument_master_provenance, research_variant_row,
)
from datetime import date
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


def test_option_expiries_ignore_missing_non_iso_and_expired_metadata():
    today = date(2026, 10, 27)
    contracts = [
        {"expiry": None},
        {"expiry": "None"},
        {"expiry": "never"},
        {"expiry": "2026-10-22"},
        {"expiry": "2026-10-29"},
        {"expiry": "2026-11-26"},
        {"expiry": "2026-11-26"},
        {"expiry": 999},
    ]
    assert eligible_expiries(contracts, today) == ["2026-10-29", "2026-11-26"]


def test_fractional_lot_or_instrument_token_is_not_truncated():
    bad = [
        {"strike": 100, "instrument_token": 123.2, "lot_size": 75, "tradingsymbol": "BADTOKEN"},
        {"strike": 100, "instrument_token": 123, "lot_size": 75.5, "tradingsymbol": "BADLOT"},
    ]
    assert choose_option_contract(bad, 100) is None


def test_atomic_research_write_preserves_first_report_and_cleans_temp(tmp_path):
    import json
    path = tmp_path / "snapback.json"
    write_research_report_once(path, {"research_only": True, "net_inr": 10})
    assert json.loads(path.read_text())["net_inr"] == 10
    with pytest.raises(FileExistsError):
        write_research_report_once(path, {"research_only": True, "net_inr": 9999})
    assert json.loads(path.read_text())["net_inr"] == 10
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_report_json_does_not_create_partial_output(tmp_path):
    path = tmp_path / "snapback.json"
    with pytest.raises(ValueError):
        write_research_report_once(path, {"research_only": True, "net_inr": float("nan")})
    assert list(tmp_path.iterdir()) == []


def test_failed_atomic_publish_cleans_temp_and_does_not_claim_evidence(tmp_path, monkeypatch):
    from app.engines.snapback import research_reporting as reporting
    path = tmp_path / "snapback.json"
    def disk_refuses_link(*args):
        raise OSError("disk full")
    monkeypatch.setattr(reporting.os, "link", disk_refuses_link)
    with pytest.raises(OSError, match="disk full"):
        write_research_report_once(path, {"research_only": True})
    assert list(tmp_path.iterdir()) == []


def test_additional_execution_cost_is_deducted_once_per_traded_unit():
    # These are synthetic trade records, NOT actual broker fills.
    historical = replay(
        trade_count=2,
        trades=[
            {"quantity": 50, "net_pnl_inr": 100.0},
            {"quantity": 25, "net_pnl_inr": 50.0},
        ],
        net_pnl_inr=150.0,
    )
    stressed = incremental_execution_cost_stress(historical)
    assert stressed["available"]
    assert stressed["total_option_units_traded"] == 75
    assert stressed["baseline_net_inr"] == 150.0
    assert stressed["extra_cost_break_even_points"] == 2.0
    assert stressed["stressed_net_inr"]["0.5"] == 112.5
    assert stressed["stressed_net_inr"]["1"] == 75.0
    assert stressed["stressed_net_inr"]["2"] == 0.0


def test_mismatched_trade_level_total_cannot_fabricate_stress_profit():
    inconsistent = replay(
        trade_count=1, trades=[{"quantity": 75, "net_pnl_inr": -100.0}],
        net_pnl_inr=1000.0,
    )
    summary = contract_replay_summary(inconsistent)
    assert summary["additional_cost_stress"]["available"] is False
    assert summary["additional_cost_stress"]["reason"] == "TRADE_PNL_TOTAL_MISMATCH"
    totals = mode_totals([dict(summary, coverage={"all_sessions_complete": True})])
    assert totals["additional_cost_stress_complete"] is False
    assert totals["additional_cost_stress_net_inr"] is None
    assert not totals["promotion_eligible"]


def test_historical_without_per_trade_receipts_cannot_claim_stress_robustness():
    summary = contract_replay_summary(replay())
    assert summary["additional_cost_stress"]["available"] is False
    assert summary["additional_cost_stress"]["reason"] == "TRADE_LEVEL_EVIDENCE_MISSING"


def test_mode_stress_and_unrounded_net_are_aggregated_without_rounding_bias():
    def contract(net):
        payload = replay(
            trade_count=1, trades=[{"quantity": 1, "net_pnl_inr": net}],
            net_pnl_inr=net, rejected_plans=0,
        )
        return dict(contract_replay_summary(payload),
                    coverage={"all_sessions_complete": True})
    rows = [contract(0.49), contract(0.49), contract(-0.01)]
    result = mode_totals(rows)
    assert result["net_inr_exact"] == 0.97
    assert result["net_inr"] == 1
    assert result["profitable_contracts"] == 2
    assert result["additional_cost_stress_complete"]
    assert result["additional_cost_stress_net_inr"]["0.5"] == -0.53
    assert result["promotion_eligible"] is False


def test_unavailable_one_trade_stress_refuses_mode_wide_stress_claim():
    complete = dict(contract_replay_summary(replay(
        trade_count=1, trades=[{"quantity": 75, "net_pnl_inr": 100}],
        net_pnl_inr=100,
    )), coverage={"all_sessions_complete": True})
    missing = dict(contract_replay_summary(replay()), coverage={"all_sessions_complete": True})
    result = mode_totals([complete, missing])
    assert result["additional_cost_stress_complete"] is False
    assert result["additional_cost_stress_net_inr"] is None


def test_invalid_cost_stress_quantity_and_nan_trade_returns_are_refused():
    for bad in [
        {"quantity": True, "net_pnl_inr": 10},
        {"quantity": 0, "net_pnl_inr": 10},
        {"quantity": 75, "net_pnl_inr": float("nan")},
        {"quantity": "75", "net_pnl_inr": 10},
    ]:
        report = incremental_execution_cost_stress(replay(
            trade_count=1, trades=[bad], net_pnl_inr=10,
        ))
        assert report["available"] is False
        assert report["reason"] == "INVALID_TRADE_EVIDENCE"


def trade_receipt(**changes):
    record = {
        "exited": True, "quantity": 75,
        "entry_time": 100.0, "exit_time": 160.0,
        "entry": 100.0, "exit_price": 103.0,
        "estimated_cost_inr": 75.0, "net_pnl_inr": 150.0,
        "exit_reason": "session_exit",
    }
    return {**record, **changes}


def test_closed_trade_evidence_is_reproducible_but_not_a_broker_receipt():
    import hashlib
    import json
    sample = replay(trade_count=1, trades=[trade_receipt()], net_pnl_inr=150.0)
    a = closed_trade_audit(sample)
    b = closed_trade_audit(sample)
    assert a == b
    assert a["closed_trades"] == 1
    assert a["net_pnl_inr_exact"] == 150
    assert not a["broker_verified"]
    raw = json.dumps(a["records"], sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest() == a["sha256"]
    assert incremental_execution_cost_stress(sample)["stressed_net_inr"]["2"] == 0


@pytest.mark.parametrize("changes", [
    {"exited": False},
    {"exit_time": 99},
    {"exit_price": 0},
    {"estimated_cost_inr": -1},
    {"quantity": 0},
    {"net_pnl_inr": 149},
    {"entry_time": float("nan")},
])
def test_research_trade_ledger_refuses_invalid_or_inconsistent_receipts(changes):
    sample = replay(trade_count=1, trades=[trade_receipt(**changes)],
                    net_pnl_inr=150.0)
    with pytest.raises(ValueError):
        closed_trade_audit(sample)


def test_research_trade_ledger_refuses_false_aggregate_profit():
    sample = replay(trade_count=1, trades=[trade_receipt()], net_pnl_inr=9000)
    with pytest.raises(ValueError, match="contradict replay"):
        closed_trade_audit(sample)


def test_absent_trade_level_rows_cannot_create_a_hash_of_fabricated_fills():
    with pytest.raises(ValueError, match="record count"):
        closed_trade_audit(replay(trade_count=5))


@pytest.mark.parametrize("other", [
    {"option": "OTHER", "error": "exchange master missing"},
    {"option": "OTHER", "skipped": "no complete session"},
    {"option": "OTHER", "trades": 0, "replay_complete": False,
     "coverage": {"all_sessions_complete": False}},
])
def test_complete_profitable_subset_does_not_create_mode_wide_stress_profit(other):
    valid = replay(
        trade_count=1, trades=[{"quantity": 75, "net_pnl_inr": 150.0}],
        net_pnl_inr=150.0,
    )
    row = dict(contract_replay_summary(valid),
               coverage={"all_sessions_complete": True})
    mode = mode_totals([row, other])
    assert mode["net_inr_exact"] == 150
    assert mode["research_complete"] is False
    assert mode["additional_cost_stress_complete"] is False
    assert mode["additional_cost_stress_net_inr"] is None
    assert mode["promotion_eligible"] is False


def test_incomplete_coverage_disqualifies_mode_wide_stress_even_if_trades_positive():
    valid = replay(trade_count=1,
                   trades=[{"quantity": 75, "net_pnl_inr": 150}],
                   net_pnl_inr=150)
    row = dict(contract_replay_summary(valid),
               coverage={"all_sessions_complete": False})
    mode = mode_totals([row])
    assert mode["trades"] == 1
    assert mode["additional_cost_stress_complete"] is False
    assert mode["additional_cost_stress_net_inr"] is None


def test_hedged_option_profit_is_not_reported_as_whole_strategy_pnl():
    simulated = replay(
        trade_count=1,
        trades=[{"quantity": 75, "net_pnl_inr": 1000.0}],
        net_pnl_inr=1000.0, hedge_mode="index_futures",
        hedge_realized_pnl_inr=999999,  # arbitrary number cannot be evidence
    )
    row = dict(contract_replay_summary(simulated),
               coverage={"all_sessions_complete": True})
    assert row["net_inr_exact"] == 1000
    assert not row["hedge_pnl_included"]
    assert not row["whole_strategy_pnl_complete"]
    assert row["whole_strategy_net_inr"] is None
    mode = mode_totals([row])
    assert mode["net_inr_exact"] == 1000  # ONLY option simulation
    assert mode["whole_strategy_net_inr"] is None
    assert not mode["whole_strategy_pnl_complete"]
    assert mode["promotion_eligible"] is False


def test_explicitly_unhedged_replay_can_report_option_only_net_without_certification():
    simulated = replay(
        trade_count=1, trades=[{"quantity": 75, "net_pnl_inr": -100}],
        net_pnl_inr=-100, hedge_mode="none",
    )
    row = dict(contract_replay_summary(simulated),
               coverage={"all_sessions_complete": True})
    assert row["whole_strategy_pnl_complete"]
    assert row["whole_strategy_net_inr"] == -100
    mode = mode_totals([row])
    assert mode["whole_strategy_net_inr"] == -100
    assert not mode["promotion_eligible"]  # no OOS/forward/broker certification


def test_current_master_hash_is_reorder_independent_and_never_historical_proof():
    current = [
        {"instrument_token": 12, "tradingsymbol": "X-CE", "name": "X",
         "exchange": "NFO", "expiry": "2026-11-26", "strike": 250,
         "lot_size": 75, "instrument_type": "CE"},
        {"instrument_token": 13, "tradingsymbol": "X-PE", "name": "X",
         "exchange": "NFO", "expiry": "2026-11-26", "strike": 250,
         "lot_size": 75, "instrument_type": "PE"},
    ]
    first = current_instrument_master_provenance(current, "2026-10-27T10:00:00+05:30")
    reordered = current_instrument_master_provenance(
        list(reversed(current)), "2026-10-27T10:00:00+05:30")
    assert first == reordered
    assert first["selected_instruments"] == 2
    assert not first["historical_date_effective"]
    assert first["retrospective_selection_bias"]
    assert not first["whole_strategy_oos_certification_eligible"]
    changed = [dict(r) for r in current]
    changed[0]["lot_size"] = 50
    later = current_instrument_master_provenance(changed, "2026-10-27T10:00:00+05:30")
    assert first["public_instrument_metadata_sha256"] != later["public_instrument_metadata_sha256"]


def test_missing_snapshot_timestamp_fails_closed():
    with pytest.raises(ValueError, match="timestamp"):
        current_instrument_master_provenance([], "")


def test_failing_challenger_does_not_erase_valid_baseline():
    metadata = {"underlying": "NIFTY", "option": "NIFTY_PE",
                "coverage": {"all_sessions_complete": True}}
    # A summary-only fixture has no closed-trade ledger and must fail.
    # Supply a real zero-trade, zero-P&L fixture for a valid audited row.
    baseline = research_variant_row(
        lambda: replay(trade_count=0, trades=[], net_pnl_inr=0.0,
                       win_rate_pct=0.0),
        metadata=metadata, policy="baseline")
    challenger = research_variant_row(
        lambda: (_ for _ in ()).throw(ValueError("candidate has no prior candles")),
        metadata=metadata, policy="snapback_intraday_cost_feasible_v1")
    assert baseline["replay_complete"] is True
    assert "error" not in baseline
    assert challenger["replay_complete"] is False
    assert "candidate has no prior candles" in challenger["error"]
    assert mode_totals([baseline])["contracts_attempted"] == 1
    comparison = mode_totals([challenger])
    assert comparison["contracts_attempted"] == 1
    assert comparison["contract_errors"] == 1
    assert comparison["promotion_eligible"] is False


def test_failing_baseline_does_not_erase_valid_challenger():
    metadata = {"underlying": "BANKNIFTY", "option": "BN_PE",
                "coverage": {"all_sessions_complete": True}}
    baseline = research_variant_row(
        lambda: (_ for _ in ()).throw(RuntimeError("baseline signal failed")),
        metadata=metadata, policy="baseline")
    observed = replay(trade_count=0, trades=[], net_pnl_inr=0.0,
                      win_rate_pct=0.0,
                      challenger_rejections={"unattainable": 7})
    challenger = research_variant_row(
        lambda: observed, metadata=metadata,
        policy="snapback_intraday_cost_feasible_v1")
    assert baseline["replay_complete"] is False
    assert challenger["replay_complete"] is True
    assert challenger["challenger_rejections"] == {"unattainable": 7}
    assert not challenger.get("error")
    assert mode_totals([baseline])["contract_errors"] == 1
    assert mode_totals([challenger])["contract_errors"] == 0


def test_invalid_or_unexpected_research_return_fails_only_that_variant():
    meta = {"underlying": "RELIANCE", "coverage": {"all_sessions_complete": True}}
    for invalid in (None, "FILLED", [], 10):
        result = research_variant_row(
            lambda invalid=invalid: invalid, metadata=meta,
            policy="snapback_intraday_cost_feasible_v1")
        assert result["replay_complete"] is False
        assert "error" in result
        assert result["underlying"] == "RELIANCE"
        assert mode_totals([result])["contracts_attempted"] == 1
